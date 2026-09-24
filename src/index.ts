import { createHash, randomUUID } from "node:crypto";
import { join } from "node:path";
import { homedir } from "node:os";
import { Type } from "typebox";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { RuntimeClient } from "./runtime-client.ts";
import { assemblePrompt, helperSignatures, advanceEvidence, needsEvidenceReview, evidenceReviewPrompt } from "./ptc-prompt.ts";
import { ModeController } from "./modes.ts";
import { PiTrace } from "./pi-trace.ts";

type RuntimeStatus = { ledger: { task_id: string; status: string; iteration: number; max_iterations: number;
	verification_attempts: number; max_verification_attempts: number;
	latest_verification?: { passed: boolean } } | null; trace_path: string; event_sequence: number };
type TaskPointer = { taskId: string; eventSequence: number };
type CellResponse = { text: string; status: string; artifact_uri: string; effect_unknown: boolean; cell_id: string;
 details: { broker_outcomes: Array<{ operation: string; status: string; changed?: boolean }> } };
export type TaskConfig = { goal?: string; criteria?: string[]; constraints?: string[];
	verification_requirements?: string[]; max_iterations?: number; max_verification_attempts?: number };
export type SkeinExtensionOptions = { task?: TaskConfig; stateDir?: string; python?: string;
 taskPacket?: boolean; evidenceReview?: boolean; codeMode?: boolean; traceExecution?: boolean; modelContract?: boolean };
export type SkeinExtensionFactory = ((pi: ExtensionAPI) => void) & { dispose: () => void };

const description = assemblePrompt(helperSignatures);
const minimalDescription = "Run a persistent Python cell with read, write, edit, bash, and verify helpers. Page retained output with result_id or more.";

export function createSkeinExtension(options: SkeinExtensionOptions = {}): SkeinExtensionFactory {
	let cleanup: (() => void) | undefined;
	return Object.assign((pi: ExtensionAPI) => { cleanup = registerSkein(pi, options); },
		{ dispose: () => cleanup?.() });
}

export default function registerDefaultSkein(pi: ExtensionAPI): void {
	registerSkein(pi, {});
}

function registerSkein(pi: ExtensionAPI, options: SkeinExtensionOptions): () => void {
	let runtime: RuntimeClient | undefined;
	let piTrace: PiTrace | undefined;
	let piTraceSession = "";
	let identity = "";
	let goal = "";
	const modes = new ModeController(pi, {
		code: options.codeMode ?? process.env.PI_SKEIN_CODE_MODE !== "0",
		trace: options.traceExecution ?? process.env.PI_SKEIN_TRACE !== "0",
		contract: options.modelContract ?? process.env.PI_SKEIN_MODEL_CONTRACT !== "0",
	});
	let mutationGeneration = 0;
	let verifiedGeneration = 0;
	let reviewQueued = false;
	const stateRoot = options.stateDir || process.env.PI_SKEIN_STATE_DIR || join(homedir(), ".pi", "skein");
	function nativeTrace(ctx: ExtensionContext): PiTrace | undefined {
		if (!modes.modes.trace) return undefined;
		const sessionId = ctx.sessionManager.getSessionId();
		if (!piTrace || piTraceSession !== sessionId) {
			piTrace = new PiTrace(stateRoot, sessionId);
			piTraceSession = sessionId;
		}
		return piTrace;
	}
	const taskConfig = options.task || (process.env.PI_SKEIN_TASK_JSON
		? JSON.parse(process.env.PI_SKEIN_TASK_JSON) as TaskConfig : {});
	if (!taskConfig || typeof taskConfig !== "object" || Array.isArray(taskConfig)) {
		throw new Error("Skein task configuration must be a JSON object");
	}
	if (taskConfig.goal !== undefined && typeof taskConfig.goal !== "string") {
		throw new Error("Skein task goal must be a string");
	}
	for (const field of ["criteria", "constraints", "verification_requirements"] as const) {
		const value = taskConfig[field];
		if (value !== undefined && (!Array.isArray(value) || value.some((item) => typeof item !== "string"))) {
			throw new Error(`Skein task ${field} must be an array of strings`);
		}
	}
	for (const field of ["max_iterations", "max_verification_attempts"] as const) {
		const value = taskConfig[field];
		if (value !== undefined && (!Number.isInteger(value) || value < 1)) {
			throw new Error(`Skein task ${field} must be a positive integer`);
		}
	}
	const verificationCommands = taskConfig.verification_requirements || (process.env.PI_SKEIN_VERIFY_COMMANDS
		? JSON.parse(process.env.PI_SKEIN_VERIFY_COMMANDS) as string[] : []);
	if (!Array.isArray(verificationCommands) || verificationCommands.some((command) => typeof command !== "string")) {
		throw new Error("PI_SKEIN_VERIFY_COMMANDS must be a JSON array of commands");
	}

	async function current(ctx: ExtensionContext): Promise<RuntimeClient> {
		const branch = ctx.sessionManager.getBranch();
		const pointer = [...branch].reverse().find((entry) => entry.type === "custom" && entry.customType === "pi-skein-task");
		const saved = pointer?.type === "custom" ? pointer.data as TaskPointer | undefined : undefined;
		const key = saved?.taskId || `${ctx.sessionManager.getSessionId()}:${randomUUID()}`;
		if (runtime && identity === key) {
			const status = await runtime.call<RuntimeStatus>("status");
			if (saved && status.event_sequence !== saved.eventSequence) {
				throw new Error("Pi branch and Skein task trace diverged; start a new task branch");
			}
			return runtime;
		}
		runtime?.close();
		runtime = new RuntimeClient(options.python);
		identity = key;
		const status = await runtime.call<RuntimeStatus>("open", {
			workspace: ctx.cwd,
			state_dir: stateRoot,
			task_id: key,
			goal: taskConfig.goal || goal || "Complete the current coding task",
			criteria: taskConfig.criteria || [taskConfig.goal || goal || "Complete the current coding task"],
			constraints: taskConfig.constraints || [],
			verification_requirements: verificationCommands,
			max_iterations: taskConfig.max_iterations || 24,
			max_verification_attempts: taskConfig.max_verification_attempts || 3,
		});
		if (saved && status.event_sequence !== saved.eventSequence) {
			runtime.close();
			runtime = undefined;
			throw new Error("Pi branch and Skein task trace diverged; start a new task branch");
		}
		if (!saved) pi.appendEntry("pi-skein-task", { taskId: key, eventSequence: status.event_sequence });
		return runtime;
	}

	async function recordPointer(client: RuntimeClient): Promise<void> {
		const status = await client.call<RuntimeStatus>("status");
		pi.appendEntry("pi-skein-task", { taskId: identity, eventSequence: status.event_sequence });
	}

	function registerCodeTool(): void { pi.registerTool({
		name: "code",
		label: "Skein Python",
		description: modes.modes.contract ? description : minimalDescription,
		promptSnippet: modes.modes.contract ? "code: persistent Python with workspace helpers; batch and reuse state" : "code: persistent Python cell",
		parameters: Type.Object({
			code: Type.Optional(Type.String()),
			result_id: Type.Optional(Type.String()),
			more: Type.Optional(Type.String()),
			offset: Type.Optional(Type.Integer({ minimum: 0 })),
			limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 51200 })),
		}),
		executionMode: "sequential",
		async execute(toolCallId, params, signal, _onUpdate, ctx) {
			const selected = Number(params.code !== undefined) + Number(params.result_id !== undefined) + Number(params.more !== undefined);
			if (selected !== 1) throw new Error("provide exactly one of code, result_id, or more");
			const client = await current(ctx);
			if (params.code !== undefined) {
				const value = await client.call<CellResponse>("execute", { code: params.code, call_id: toolCallId }, signal);
				[mutationGeneration, verifiedGeneration] = advanceEvidence(value.details.broker_outcomes.map((outcome) => ({
					...outcome, changed: (outcome as { changed?: boolean; complete?: boolean }).changed,
				})), mutationGeneration, verifiedGeneration);
				await recordPointer(client);
				return { content: [{ type: "text" as const, text: value.text }], details: value.details };
			}
			const value = await client.call<{ text: string; next_offset: number | null }>("page", {
				uri: params.result_id || params.more, offset: params.offset || 0, limit: params.limit || 16000,
			}, signal);
			return { content: [{ type: "text" as const, text: value.text }], details: value };
		},
	}); }
	registerCodeTool();

	pi.on("before_agent_start", (event) => {
		if (!goal) goal = event.prompt;
	});
	pi.on("context_with_system", async (event, ctx) => {
		if ((!modes.modes.code && !modes.modes.trace) || !event.messages.length || event.messages[0].role !== "system") return;
		const packet = modes.modes.code && modes.modes.contract && options.taskPacket ? await (await current(ctx)).call<{ content: string; content_hash: string; source_watermark: number }>("project_context", {
			max_tokens: 20000,
		}) : undefined;
		const messages = packet ? [event.messages[0],
			{ role: "system" as const, content: packet.content, timestamp: Date.now() },
			...event.messages.slice(1)] : event.messages;
		const trace = nativeTrace(ctx);
		if (trace) {
			const retained = trace.retain(messages);
			trace.append("pi.context_prepared", { ...retained, message_count: messages.length,
				code_mode: modes.modes.code, model_contract: modes.modes.code && modes.modes.contract,
				contract_sha256: modes.modes.code ? createHash("sha256").update(modes.modes.contract ? description : minimalDescription).digest("hex") : null,
				packet_hash: packet?.content_hash ?? null });
			if (modes.modes.code) {
				const client = await current(ctx);
				await client.call("record", { kind: "pi.context_prepared", payload: {
					program: "pi-skein-context@3", packet_hash: packet?.content_hash ?? null,
					packet_source_watermark: packet?.source_watermark ?? null,
					context_sha256: retained.sha256, message_count: messages.length,
				} });
				await recordPointer(client);
			}
		}
		if (packet) return { messages };
	});
	pi.on("agent_start", async (_event, ctx) => {
		nativeTrace(ctx)?.append("pi.agent_started", { code_mode: modes.modes.code });
		if (!modes.modes.code || !modes.modes.trace) return;
		const client = await current(ctx);
		await client.call("record", { kind: "pi.agent_started", payload: {} });
		await recordPointer(client);
	});
	pi.on("message_end", async (event, ctx) => {
		const message = event.message;
		const trace = nativeTrace(ctx);
		if (trace) trace.append("pi.message_completed", { role: message.role,
			...trace.retain(message) });
		if (!modes.modes.code || !modes.modes.trace) return;
		const client = await current(ctx);
		const payload: Record<string, unknown> = {
			role: message.role,
			content_sha256: createHash("sha256").update(JSON.stringify("content" in message ? message.content : [])).digest("hex"),
		};
		if (message.role === "assistant") {
			payload.stop_reason = message.stopReason;
			payload.usage = message.usage;
		}
		await client.call("record", { kind: "pi.message_completed", payload });
		await recordPointer(client);
	});
	pi.on("tool_call", (event, ctx) => {
		const trace = nativeTrace(ctx);
		if (trace) trace.append("pi.tool_started", { tool_call_id: event.toolCallId,
			tool_name: event.toolName, input: trace.retain(event.input) });
		if (modes.modes.code && event.toolName !== "code") {
			trace?.append("pi.tool_blocked", { tool_call_id: event.toolCallId,
				tool_name: event.toolName, reason: "code mode permits code only" });
			return { block: true, reason: "Skein mode permits the code tool only" };
		}
	});
	pi.on("tool_result", (event, ctx) => {
		const trace = nativeTrace(ctx);
		if (trace) trace.append("pi.tool_completed", { tool_call_id: event.toolCallId,
			tool_name: event.toolName, is_error: event.isError,
			output: trace.retain({ content: event.content, details: event.details, usage: event.usage }) });
	});
	pi.on("user_bash", async (event, ctx) => {
		if (!modes.modes.code) return;
		const trace = nativeTrace(ctx);
		const userShellId = randomUUID();
		trace?.append("pi.user_bash_started", { operation_id: userShellId, command: trace.retain(event.command) });
		const client = await current(ctx);
		const value = await client.call<{ data: { output: string; exit_code: number } }>("shell", { command: event.command });
		trace?.append("pi.user_bash_completed", { operation_id: userShellId,
			exit_code: value.data.exit_code, output: trace.retain(value.data.output) });
		await recordPointer(client);
		return { result: { output: value.data.output, exitCode: value.data.exit_code,
			cancelled: false, truncated: false } };
	});
	pi.on("session_shutdown", () => { runtime?.close(); runtime = undefined; identity = ""; piTrace = undefined; piTraceSession = ""; });
	pi.on("session_start", (_event, ctx) => {
		runtime?.close(); runtime = undefined; identity = ""; goal = "";
		piTrace = undefined; piTraceSession = "";
		mutationGeneration = 0; verifiedGeneration = 0; reviewQueued = false;
		modes.startSession();
		nativeTrace(ctx)?.append("pi.session_started", { code_mode: modes.modes.code,
			model_contract: modes.modes.contract });
	});
	pi.on("agent_before_settle", async (event, ctx) => {
		nativeTrace(ctx)?.append("pi.agent_before_settle", { outcome: event.outcome });
		if (!modes.modes.code || !runtime) return;
		if (event.outcome !== "completed") {
			await runtime.call("finish", { status: event.outcome === "aborted" ? "cancelled" : "failed",
				reason: `Pi agent ${event.outcome}` });
			await recordPointer(runtime);
			return;
		}
		if (verificationCommands.length === 0) return;
		const status = await runtime.call<RuntimeStatus>("status");
		if (status.ledger?.status === "complete") return;
		const report = await runtime.call<{ passed: boolean; commands: { command: string; exit_code: number }[] }>("verify", {
			commands: verificationCommands,
		});
		await recordPointer(runtime);
		if (report.passed) return;
		const updated = await runtime.call<RuntimeStatus>("status");
		if (!event.context.canContinue || !updated.ledger
			|| updated.ledger.iteration >= updated.ledger.max_iterations
			|| updated.ledger.verification_attempts >= updated.ledger.max_verification_attempts) {
			await runtime.call("finish", { status: "blocked", reason: "Verification failed or repair budget exhausted" });
			await recordPointer(runtime);
			return;
		}
		const failures = report.commands.map(({ command, exit_code }) =>
			`- ${command}: ${exit_code === 0 ? "passed" : `exit ${exit_code}`}`).join("\n");
		return { entries: [{ type: "custom_message" as const, customType: "skein-verification",
			content: `Host verification failed.\n${failures}\nFix the failure and verify again.`,
			display: true }], continue: true };
	});
	if (options.evidenceReview ?? process.env.PTC_EVIDENCE_REVIEW === "1") {
		pi.on("agent_end", async (event) => {
			if (!modes.modes.code || !modes.modes.contract || reviewQueued) return;
			const finalText = event.messages.filter((message) => message.role === "assistant")
				.flatMap((message) => message.content ?? []).filter((part) => part.type === "text")
				.map((part) => part.text).join("\n");
			if (!needsEvidenceReview(finalText, mutationGeneration, verifiedGeneration)) return;
			reviewQueued = true;
			pi.sendMessage({ customType: "ptc-evidence-review", display: true,
				content: evidenceReviewPrompt }, { deliverAs: "followUp", triggerTurn: true });
		});
	}

	pi.registerCommand("skein", {
		description: "Skein code, trace, contract, status, and verification",
		async handler(args, ctx) {
			const [action, ...rest] = args.trim().split(/\s+/);
			const component = action === "on" || action === "off" ? "code" : action;
			const setting = action === "on" || action === "off" ? action : rest[0];
			if (["code", "trace", "contract"].includes(component)) {
				if (setting !== "on" && setting !== "off") throw new Error(`usage: /skein ${component} on|off`);
				const on = setting === "on";
				if (component === "code") modes.setCode(on);
				if (component === "trace") {
					if (!on) nativeTrace(ctx)?.append("pi.trace_disabled", {});
					modes.setTrace(on);
					if (on) nativeTrace(ctx)?.append("pi.trace_enabled", {});
				}
				if (component === "contract") {
					modes.setContract(on);
					registerCodeTool();
					nativeTrace(ctx)?.append("pi.contract_changed", { enabled: on });
				}
				if (component === "code" && !on && !pi.getActiveTools().includes("bash")) {
					ctx.ui.notify("Pi's Bash tool is unavailable in this session; restart without --tools code to restore the default tools", "warning");
					return;
				}
				ctx.ui.notify(`Skein ${component} ${setting}`, "info");
				return;
			}
			if (!action || action === "status") {
				const trace = nativeTrace(ctx);
				const modeStatus = `code ${modes.modes.code ? "on" : "off"}, trace ${modes.modes.trace ? "on" : "off"}, contract ${modes.modes.contract ? "on" : "off"}`;
				const saved = ctx.sessionManager.getBranch().some((entry) => entry.type === "custom" && entry.customType === "pi-skein-task");
				if (!runtime && !saved) { ctx.ui.notify(`Skein: ${modeStatus}; ${trace ? `Pi trace ${trace.path}` : "no task"}`, "info"); return; }
				const client = await current(ctx);
				const status = await client.call<RuntimeStatus>("status");
				ctx.ui.notify(`Skein: ${modeStatus}; task ${status.ledger?.status || "uninitialized"}; trace ${status.trace_path}`, "info");
				return;
			}
			if (action !== "verify") throw new Error("usage: /skein [status|code on|off|trace on|off|contract on|off|verify <command>]");
			const client = await current(ctx);
			if (action === "verify") {
				const command = rest.join(" ");
				if (!command) throw new Error("usage: /skein verify <command>");
				const report = await client.call<{ passed: boolean }>("verify", { commands: [command] });
				await recordPointer(client);
				ctx.ui.notify(report.passed ? "Skein verification passed" : "Skein verification failed", report.passed ? "info" : "error");
				return;
			}
		},
	});
	return () => { runtime?.close(); runtime = undefined; identity = ""; piTrace = undefined; piTraceSession = ""; };
}

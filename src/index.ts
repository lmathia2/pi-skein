import { createHash, randomUUID } from "node:crypto";
import { join } from "node:path";
import { homedir } from "node:os";
import { Type } from "typebox";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { RuntimeClient } from "./runtime-client.ts";
import { assemblePrompt, helperSignatures, advanceEvidence, needsEvidenceReview, evidenceReviewPrompt } from "./ptc-prompt.ts";

type RuntimeStatus = { ledger: { task_id: string; status: string; iteration: number; max_iterations: number;
	verification_attempts: number; max_verification_attempts: number;
	latest_verification?: { passed: boolean } } | null; trace_path: string; event_sequence: number };
type TaskPointer = { taskId: string; eventSequence: number };
type CellResponse = { text: string; status: string; artifact_uri: string; effect_unknown: boolean; cell_id: string;
 details: { broker_outcomes: Array<{ operation: string; status: string; changed?: boolean }> } };
export type TaskConfig = { goal?: string; criteria?: string[]; constraints?: string[];
	verification_requirements?: string[]; max_iterations?: number; max_verification_attempts?: number };
export type SkeinExtensionOptions = { task?: TaskConfig; stateDir?: string; python?: string; taskPacket?: boolean; evidenceReview?: boolean };
export type SkeinExtensionFactory = ((pi: ExtensionAPI) => void) & { dispose: () => void };

const description = assemblePrompt(helperSignatures);

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
	let identity = "";
	let goal = "";
	let enabled = true;
	let previousTools: string[] = [];
	let mutationGeneration = 0;
	let verifiedGeneration = 0;
	let reviewQueued = false;
	const stateRoot = options.stateDir || process.env.PI_SKEIN_STATE_DIR || join(homedir(), ".pi", "skein");
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

	pi.registerTool({
		name: "code",
		label: "Skein Python",
		description,
		promptSnippet: "code: persistent Python with workspace helpers; batch and reuse state",
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
	});

	pi.on("before_agent_start", (event) => {
		if (!goal) goal = event.prompt;
	});
	pi.on("context_with_system", async (event, ctx) => {
		if (!enabled || !event.messages.length || event.messages[0].role !== "system") return;
		const client = await current(ctx);
		const packet = options.taskPacket ? await client.call<{ content: string; content_hash: string; source_watermark: number }>("project_context", {
			max_tokens: 20000,
		}) : undefined;
		const messages = packet ? [event.messages[0],
			{ role: "system" as const, content: packet.content, timestamp: Date.now() },
			...event.messages.slice(1)] : event.messages;
		await client.call("record", { kind: "pi.context_prepared", payload: {
			program: "pi-skein-context@2", packet_hash: packet?.content_hash ?? null,
			packet_source_watermark: packet?.source_watermark ?? null,
			stable_prompt_sha256: createHash("sha256").update(JSON.stringify(messages[0])).digest("hex"),
			context_sha256: createHash("sha256").update(JSON.stringify(messages)).digest("hex"),
			message_count: messages.length,
		} });
		await recordPointer(client);
		if (packet) return { messages };
	});
	pi.on("agent_start", async (_event, ctx) => {
		if (!enabled) return;
		const client = await current(ctx);
		await client.call("record", { kind: "pi.agent_started", payload: {} });
		await recordPointer(client);
	});
	pi.on("message_end", async (event, ctx) => {
		if (!enabled) return;
		const client = await current(ctx);
		const message = event.message;
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
	pi.on("tool_call", (event) => {
		if (enabled && event.toolName !== "code") {
			return { block: true, reason: "Skein mode permits the code tool only" };
		}
	});
	pi.on("user_bash", async (event, ctx) => {
		if (!enabled) return;
		const client = await current(ctx);
		const value = await client.call<{ data: { output: string; exit_code: number } }>("shell", { command: event.command });
		await recordPointer(client);
		return { result: { output: value.data.output, exitCode: value.data.exit_code,
			cancelled: false, truncated: false } };
	});
	pi.on("session_shutdown", () => { runtime?.close(); runtime = undefined; identity = ""; });
	pi.on("session_start", () => {
		runtime?.close(); runtime = undefined; identity = ""; goal = "";
		mutationGeneration = 0; verifiedGeneration = 0; reviewQueued = false;
		if (enabled) {
			previousTools = pi.getActiveTools();
			pi.setActiveTools(["code"]);
		}
	});
	pi.on("agent_before_settle", async (event) => {
		if (!enabled || !runtime) return;
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
			if (!enabled || reviewQueued) return;
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
		description: "Skein status, activation, and verification",
		async handler(args, ctx) {
			const [action, ...rest] = args.trim().split(/\s+/);
			if (action === "off") {
				enabled = false;
				pi.setActiveTools(previousTools);
				ctx.ui.notify("Skein disabled", "info");
				return;
			}
			if (action === "on") { enabled = true; pi.setActiveTools(["code"]); ctx.ui.notify("Skein enabled", "info"); return; }
			if ((!action || action === "status") && !runtime
				&& !ctx.sessionManager.getBranch().some((entry) => entry.type === "custom" && entry.customType === "pi-skein-task")) {
				ctx.ui.notify("Skein: no task started", "info");
				return;
			}
			const client = await current(ctx);
			if (action === "verify") {
				const command = rest.join(" ");
				if (!command) throw new Error("usage: /skein verify <command>");
				const report = await client.call<{ passed: boolean }>("verify", { commands: [command] });
				await recordPointer(client);
				ctx.ui.notify(report.passed ? "Skein verification passed" : "Skein verification failed", report.passed ? "info" : "error");
				return;
			}
			const status = await client.call<RuntimeStatus>("status");
			ctx.ui.notify(`Skein: ${status.ledger?.status || "uninitialized"}; trace ${status.trace_path}`, "info");
		},
	});
	return () => { runtime?.close(); runtime = undefined; identity = ""; };
}

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

export interface SkeinTaskPointer { taskId: string; eventSequence: number }
export interface SkeinEvidence {
	taskId: string;
	eventCount: number;
	tracePath: string;
	outcome: { status: string; verification?: { passed: boolean } } | null;
	verification: { passed: boolean } | null;
	unresolvedEffects: string[];
}

export interface PiTraceEvent {
	schema_version: number;
	sequence: number;
	event_id: string;
	timestamp: string;
	kind: string;
	payload: Record<string, unknown>;
}

export function getPiTracePath(stateDir: string, sessionId: string): string {
	const name = createHash("sha256").update(sessionId).digest("hex");
	return join(stateDir, "pi-trace", name, "events.jsonl");
}

export function readPiTrace(stateDir: string, sessionId: string): { path: string; events: PiTraceEvent[] } {
	const path = getPiTracePath(stateDir, sessionId);
	const events = readFileSync(path, "utf8").split("\n").filter(Boolean)
		.map((line) => JSON.parse(line) as PiTraceEvent);
	for (let index = 0; index < events.length; index++) {
		if (events[index].schema_version !== 1 || events[index].sequence !== index + 1) {
			throw new Error("Pi trace has an invalid schema or sequence gap");
		}
	}
	return { path, events };
}

export function readPiTraceArtifact(stateDir: string, sessionId: string, uri: string): unknown {
	const sha = uri.startsWith("artifact://sha256/") ? uri.slice("artifact://sha256/".length) : "";
	if (!/^[a-f0-9]{64}$/.test(sha)) throw new Error("Invalid Pi trace artifact URI");
	const directory = join(stateDir, "pi-trace", createHash("sha256").update(sessionId).digest("hex"), "artifacts");
	const body = readFileSync(join(directory, sha), "utf8");
	if (createHash("sha256").update(body).digest("hex") !== sha) throw new Error("Pi trace artifact integrity failure");
	return JSON.parse(body) as unknown;
}

export function getSkeinTaskPointer(branch: readonly { type: string; customType?: string; data?: unknown }[]): SkeinTaskPointer | null {
	const entry = [...branch].reverse().find((item) => item.type === "custom" && item.customType === "pi-skein-task");
	if (!entry || !entry.data || typeof entry.data !== "object") return null;
	const data = entry.data as Partial<SkeinTaskPointer>;
	if (typeof data.taskId !== "string" || !Number.isInteger(data.eventSequence)) return null;
	return { taskId: data.taskId, eventSequence: data.eventSequence! };
}

export function readSkeinEvidence(stateDir: string, taskId: string): SkeinEvidence {
	const name = createHash("sha256").update(taskId).digest("hex");
	const tracePath = join(stateDir, name, "events.jsonl");
	const events = readFileSync(tracePath, "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line) as {
		sequence: number; kind: string; payload: Record<string, unknown>;
	});
	const pending = new Set<string>();
	let outcome: SkeinEvidence["outcome"] = null;
	let verification: SkeinEvidence["verification"] = null;
	for (let index = 0; index < events.length; index++) {
		const event = events[index];
		if (event.sequence !== index + 1) throw new Error("Skein event stream has a sequence gap");
		const id = event.payload.cell_id || event.payload.operation_id;
		if ((event.kind === "cell.started" || event.kind === "effect.started") && typeof id === "string") pending.add(id);
		if ((event.kind === "cell.completed" || event.kind === "effect.completed") && typeof id === "string") {
			if (event.payload.effect_unknown === true) pending.add(id);
			else pending.delete(id);
		}
		if (event.kind === "verification.completed") verification = event.payload as SkeinEvidence["verification"];
		if (event.kind === "task.finished") outcome = event.payload as SkeinEvidence["outcome"];
	}
	if (outcome?.status === "complete" && (!verification?.passed || pending.size > 0)) {
		throw new Error("Skein completion lacks valid verification evidence");
	}
	return { taskId, eventCount: events.length, tracePath, outcome, verification,
		unresolvedEffects: [...pending].sort() };
}

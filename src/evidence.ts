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

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";

interface Pending {
	resolve: (value: unknown) => void;
	reject: (error: Error) => void;
}

const pythonPath = fileURLToPath(new URL("../python", import.meta.url));

export class RuntimeClient {
	private process: ChildProcessWithoutNullStreams;
	private pending = new Map<string, Pending>();
	private buffer = "";
	private queue: Promise<unknown> = Promise.resolve();
	private closed = false;

	constructor(executable = process.env.PI_SKEIN_PYTHON || "python3") {
		this.process = spawn(executable, ["-m", "pi_skein.server"], {
			env: { ...process.env, PYTHONPATH: pythonPath },
			stdio: ["pipe", "pipe", "pipe"],
		});
		this.process.unref();
		this.process.stdout.setEncoding("utf8");
		this.process.stdout.on("data", (chunk: string) => this.consume(chunk));
		let stderr = "";
		this.process.stderr.setEncoding("utf8");
		this.process.stderr.on("data", (chunk: string) => { stderr = (stderr + chunk).slice(-4000); });
		this.process.on("error", (error) => this.fail(error));
		this.process.on("exit", (code) => this.fail(new Error(`Skein runtime exited (${code}): ${stderr}`)));
		this.refIO(false);
	}

	private refIO(active: boolean): void {
		for (const stream of [this.process.stdin, this.process.stdout, this.process.stderr]) {
			const handle = stream as typeof stream & { ref?: () => void; unref?: () => void };
			if (active) handle.ref?.();
			else handle.unref?.();
		}
	}

	private consume(chunk: string): void {
		this.buffer += chunk;
		for (;;) {
			const end = this.buffer.indexOf("\n");
			if (end < 0) return;
			const line = this.buffer.slice(0, end);
			this.buffer = this.buffer.slice(end + 1);
			try {
				const response = JSON.parse(line) as { id: string; ok: boolean; value?: unknown; error?: string };
				const pending = this.pending.get(response.id);
				if (!pending) continue;
				this.pending.delete(response.id);
				if (this.pending.size === 0) this.refIO(false);
				if (response.ok) pending.resolve(response.value);
				else pending.reject(new Error(response.error || "Skein runtime error"));
			} catch (error) {
				this.fail(error instanceof Error ? error : new Error(String(error)));
			}
		}
	}

	private fail(error: Error): void {
		this.closed = true;
		for (const pending of this.pending.values()) pending.reject(error);
		this.pending.clear();
		this.refIO(false);
	}

	private request(method: string, params: object = {}, signal?: AbortSignal): Promise<unknown> {
		if (this.closed) return Promise.reject(new Error("Skein runtime is closed"));
		const id = randomUUID();
		return new Promise((resolve, reject) => {
			const abort = () => {
				this.process.kill("SIGTERM");
				const force = setTimeout(() => this.process.kill("SIGKILL"), 2000);
				force.unref();
				reject(new Error("Skein operation cancelled; effects may require reconciliation"));
			};
			if (signal?.aborted) { abort(); return; }
			const pending: Pending = {
				resolve: (value) => { signal?.removeEventListener("abort", abort); resolve(value); },
				reject: (error) => { signal?.removeEventListener("abort", abort); reject(error); },
			};
			this.pending.set(id, pending);
			this.refIO(true);
			signal?.addEventListener("abort", abort, { once: true });
			this.process.stdin.write(JSON.stringify({ version: 1, id, method, params }) + "\n");
		});
	}

	call<T>(method: string, params: object = {}, signal?: AbortSignal): Promise<T> {
		const next = this.queue.then(() => this.request(method, params, signal)) as Promise<T>;
		this.queue = next.catch(() => undefined);
		return next;
	}

	close(): void {
		if (this.closed) return;
		this.closed = true;
		this.process.stdin.end();
		this.process.kill();
	}
}

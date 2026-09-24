import { createHash, randomUUID } from "node:crypto";
import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, writeSync } from "node:fs";
import { join } from "node:path";

function sha256(body: string): string {
  return createHash("sha256").update(body).digest("hex");
}

function writeAll(fd: number, text: string): void {
  const data = Buffer.from(text);
  for (let offset = 0; offset < data.length;) {
    const written = writeSync(fd, data, offset, data.length - offset);
    if (written <= 0) throw new Error("Pi trace write made no progress");
    offset += written;
  }
}

export class PiTrace {
  readonly path: string;
  private readonly artifacts: string;
  private sequence = 0;

  constructor(stateRoot: string, sessionId: string) {
    const directory = join(stateRoot, "pi-trace", sha256(sessionId));
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    this.path = join(directory, "events.jsonl");
    this.artifacts = join(directory, "artifacts");
    mkdirSync(this.artifacts, { recursive: true, mode: 0o700 });
    if (existsSync(this.path)) {
      const lines = readFileSync(this.path, "utf8").split("\n").filter(Boolean);
      for (const line of lines) {
        const event = JSON.parse(line) as { sequence: number; schema_version: number };
        if (event.schema_version !== 1 || event.sequence !== ++this.sequence) {
          throw new Error("Pi trace has an invalid schema or sequence gap");
        }
      }
    }
  }

  get eventCount(): number { return this.sequence; }

  retain(value: unknown): { artifact_uri: string; sha256: string; bytes: number; capture_complete: boolean } {
    let body: string;
    let captureComplete = true;
    try { body = JSON.stringify(value) ?? "null"; }
    catch (error) {
      body = JSON.stringify({ capture_error: error instanceof Error ? error.message : String(error) });
      captureComplete = false;
    }
    const hash = sha256(body);
    const path = join(this.artifacts, hash);
    if (!existsSync(path)) {
      const temporary = path + "." + randomUUID() + ".tmp";
      const fd = openSync(temporary, "wx", 0o600);
      try { writeAll(fd, body); fsyncSync(fd); } finally { closeSync(fd); }
      renameSync(temporary, path);
    }
    return { artifact_uri: `artifact://sha256/${hash}`, sha256: hash,
      bytes: Buffer.byteLength(body), capture_complete: captureComplete };
  }

  append(kind: string, payload: Record<string, unknown>): void {
    const event = { schema_version: 1, sequence: this.sequence + 1,
      event_id: randomUUID(), timestamp: new Date().toISOString(), kind, payload };
    const fd = openSync(this.path, "a", 0o600);
    try { writeAll(fd, JSON.stringify(event) + "\n"); fsyncSync(fd); }
    finally { closeSync(fd); }
    this.sequence++;
  }
}

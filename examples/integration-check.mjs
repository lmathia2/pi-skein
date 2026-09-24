import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import {
  createAgentSession, DefaultResourceLoader, SessionManager,
} from "@earendil-works/pi-coding-agent";
import { getSkeinTaskPointer, readSkeinEvidence } from "../src/evidence.ts";

const root = mkdtempSync(join(tmpdir(), "pi-skein-integration-"));
process.env.PI_SKEIN_STATE_DIR = join(root, "state");
const resourceLoader = new DefaultResourceLoader({
  cwd: root,
  agentDir: join(root, "agent"),
  additionalExtensionPaths: [resolve(process.env.PI_SKEIN_EXTENSION_PATH || "src/index.ts")],
  noExtensions: true,
});
await resourceLoader.reload();
const { session } = await createAgentSession({
  cwd: root,
  agentDir: join(root, "agent"),
  resourceLoader,
  sessionManager: SessionManager.inMemory(root),
});
try {
  await session.bindExtensions({});
  const tool = session.agent.state.tools.find((item) => item.name === "code");
  if (!tool) throw new Error("code tool unavailable");
  const first = await tool.execute("cell-1", { code: "value = 9\nprint(write('result.txt', 'ok'))" });
  if (!first.content[0].text.includes("changed")) throw new Error("write result missing");
  const second = await tool.execute("cell-2", { code: "print(value * 2)" });
  if (second.content[0].text !== "18\n") throw new Error(`persistent state failed: ${second.content[0].text}`);
  if (readFileSync(join(root, "result.txt"), "utf8") !== "ok") throw new Error("workspace effect missing");
  const pointer = getSkeinTaskPointer(session.sessionManager.getBranch());
  if (!pointer) throw new Error("Pi session lacks Skein task pointer");
  const evidence = readSkeinEvidence(join(root, "state"), pointer.taskId);
  if (evidence.eventCount < 5 || evidence.unresolvedEffects.length !== 0) {
    throw new Error("Skein trace is incomplete");
  }
  console.log("Pi extension executed persistent code and brokered a write");
} finally {
  session.dispose();
  rmSync(root, { recursive: true, force: true });
}

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createAgentSession, DefaultResourceLoader, SessionManager } from "@earendil-works/pi-coding-agent";
import { createSkeinExtension } from "../src/index.ts";

const workspace = mkdtempSync(join(tmpdir(), "pi-skein-sdk-"));
const extension = createSkeinExtension({
  task: { goal: "Example coding task", criteria: ["Task verified"], verification_requirements: ["true"] },
  stateDir: join(workspace, "skein-state"),
  python: process.env.PI_SKEIN_PYTHON || "python3",
});
const resourceLoader = new DefaultResourceLoader({
  cwd: workspace,
  agentDir: join(workspace, "agent"),
  extensionFactories: [extension],
  noExtensions: true,
});
await resourceLoader.reload();
const { session } = await createAgentSession({
  cwd: workspace,
  resourceLoader,
  sessionManager: SessionManager.inMemory(workspace),
});
try {
  await session.bindExtensions({});
  if (session.getActiveToolNames().join(",") !== "code") throw new Error("Skein tool selection failed");
  console.log("Pi SDK loaded an independently configured pi-skein extension");
} finally {
  session.dispose();
  extension.dispose();
  rmSync(workspace, { recursive: true, force: true });
}

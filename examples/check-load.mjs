import { resolve } from "node:path";
import {
  createAgentSession,
  DefaultResourceLoader,
  getAgentDir,
  SessionManager,
} from "@earendil-works/pi-coding-agent";

const resourceLoader = new DefaultResourceLoader({
  cwd: process.cwd(),
  agentDir: getAgentDir(),
  additionalExtensionPaths: [resolve(process.env.PI_SKEIN_EXTENSION_PATH || "src/index.ts")],
  noExtensions: true,
});
await resourceLoader.reload();
const { session } = await createAgentSession({ resourceLoader, sessionManager: SessionManager.inMemory() });
try {
  await session.bindExtensions({});
  if (!session.getActiveToolNames().includes("code")) throw new Error("Skein code tool was not loaded");
  if (session.getActiveToolNames().some((name) => name !== "code")) {
    throw new Error(`Unexpected active tools: ${session.getActiveToolNames().join(", ")}`);
  }
  console.log("Pi loaded pi-skein code tool");
} finally {
  session.dispose();
}

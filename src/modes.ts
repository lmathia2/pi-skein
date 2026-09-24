import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export interface SkeinModes {
  code: boolean;
  trace: boolean;
  contract: boolean;
}

export const PI_DEFAULT_TOOLS = ["read", "bash", "edit", "write"] as const;

/** The tools Pi had before code mode took over, or its built-in coding set. */
export function fallbackTools(pi: ExtensionAPI, previous: string[]): string[] {
  const available = new Set(pi.getAllTools().map((tool) => tool.name));
  const restored = previous.filter((name) => name !== "code" && available.has(name));
  return restored.length ? restored : PI_DEFAULT_TOOLS.filter((name) => available.has(name));
}

export class ModeController {
  readonly modes: SkeinModes;
  private previousTools: string[] = [];
  private readonly pi: ExtensionAPI;

  constructor(pi: ExtensionAPI, initial: SkeinModes) {
    this.pi = pi;
    this.modes = { ...initial };
  }

  startSession(): void {
    this.previousTools = this.pi.getActiveTools().filter((name) => name !== "code");
    this.applyCodeTools();
  }

  setCode(enabled: boolean): void {
    if (enabled === this.modes.code) return;
    if (enabled) {
      const active = this.pi.getActiveTools().filter((name) => name !== "code");
      if (active.length) this.previousTools = active;
    }
    this.modes.code = enabled;
    this.applyCodeTools();
  }

  setTrace(enabled: boolean): void { this.modes.trace = enabled; }
  setContract(enabled: boolean): void { this.modes.contract = enabled; }

  private applyCodeTools(): void {
    this.pi.setActiveTools(this.modes.code ? ["code"] : fallbackTools(this.pi, this.previousTools));
  }
}

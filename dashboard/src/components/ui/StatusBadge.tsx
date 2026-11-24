import { Cloud, Cpu } from "lucide-react";
import { STATUS_LABEL } from "../../lib/format";
import type { RunStatus } from "../../types";
import { Badge, type Tone } from "./primitives";

export const STATUS_TONE: Record<RunStatus, Tone> = {
  created: "gray",
  running: "blue",
  awaiting_human: "amber",
  paused_provider_unavailable: "orange",
  failed: "red",
  completed: "green",
  rejected: "red",
  aborted: "gray",
};

export function StatusBadge({
  status,
  stalled,
  outcome,
  className,
}: {
  status: RunStatus;
  stalled?: boolean;
  outcome?: string | null;
  className?: string;
}) {
  if (stalled && (status === "running" || status === "created"))
    return (
      <Badge tone="amber" dot className={className} title="Nothing is running this any more. You can stop it.">
        Stalled
      </Badge>
    );
  if (status === "aborted" && outcome === "cancelled")
    return (
      <Badge tone="gray" dot className={className}>
        Stopped
      </Badge>
    );
  const live = status === "running" || status === "awaiting_human";
  return (
    <Badge tone={STATUS_TONE[status] ?? "gray"} dot pulse={live} className={className}>
      {STATUS_LABEL[status] ?? status}
    </Badge>
  );
}

export function ProviderBadge({ provider, className }: { provider: string; className?: string }) {
  const isLocal = provider === "ollama";
  return (
    <Badge
      tone={isLocal ? "violet" : "cyan"}
      icon={isLocal ? <Cpu className="h-3 w-3" /> : <Cloud className="h-3 w-3" />}
      className={className}
      title={isLocal ? "Ollama (local)" : "Gemini (cloud)"}
    >
      {isLocal ? "Ollama" : provider === "gemini" ? "Gemini" : provider}
    </Badge>
  );
}

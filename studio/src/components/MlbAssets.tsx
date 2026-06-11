import { useState } from "react";
import { BREF_TO_MLBAM, mlbPlayerPhotoUrl, mlbTeamLogoUrl } from "../api.ts";

interface PlayerPhotoProps {
  mlbId: number | string;
  size?: number;
  className?: string;
}

export function PlayerPhoto({ mlbId, size = 72, className }: PlayerPhotoProps) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <img
      src={mlbPlayerPhotoUrl(mlbId)}
      alt=""
      width={size}
      height={size}
      className={className}
      style={{
        borderRadius: "50%",
        objectFit: "cover",
        objectPosition: "50% 15%",
        background: "var(--bg-panel)",
        border: "1px solid rgba(255,255,255,0.08)",
        flexShrink: 0,
      }}
      onError={() => setFailed(true)}
    />
  );
}

interface TeamLogoProps {
  brefCode: string;
  size?: number;
  opacity?: number;
  className?: string;
}

export function TeamLogo({
  brefCode,
  size = 24,
  opacity = 0.85,
  className,
}: TeamLogoProps) {
  const [failed, setFailed] = useState(false);
  const mlbamId = BREF_TO_MLBAM[brefCode?.toUpperCase()];
  if (!mlbamId || failed) return null;
  return (
    <img
      src={mlbTeamLogoUrl(mlbamId)}
      alt={brefCode}
      width={size}
      height={size}
      className={className}
      style={{ display: "inline-block", verticalAlign: "middle", opacity, flexShrink: 0 }}
      onError={() => setFailed(true)}
    />
  );
}

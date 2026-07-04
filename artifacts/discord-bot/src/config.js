// ═══════════════════════════════════════════
//  NOIR/TERMINAL BOT CONFIG
// ═══════════════════════════════════════════

export const COLORS = {
  PRIMARY: 0x050505,    // Near-black — default embed
  SUCCESS: 0x1a1a1a,    // Dark gray — success
  WARNING: 0x2a2a2a,    // Mid gray — warning
  DANGER: 0x0d0d0d,     // Black — danger / punish
  INFO: 0x111111,       // Very dark — info
  ACCENT: 0xc0c0c0,     // Silver — highlights / accent
};

export const PREFIX = '+';

export const NUKE_CONFIG = {
  THRESHOLD: 3,         // max destructive actions
  INTERVAL: 10_000,     // within 10 seconds
};

export const SPAM_CONFIG = {
  MESSAGE_LIMIT: 5,
  INTERVAL: 3_000,
};

export const ALT_CONFIG = {
  MIN_AGE_DAYS: 7,
};

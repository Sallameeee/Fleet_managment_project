/**
 * Short chime when a NEW manager notification arrives.
 *
 * Rules (mirroring the mobile app):
 *   * BASELINE  — the first unread count seen is remembered silently, so the
 *     notifications that already existed on page load never make a sound.
 *   * INCREASE ONLY — a chime fires only when the count goes UP. Re-polling the
 *     same value, or the count dropping (the manager read them), is silent.
 *   * ONE PER BURST — a minimum gap between chimes, so several notifications
 *     landing in one poll produce one sound.
 *
 * Autoplay: browsers reject audio until the user has interacted with the page.
 * We swallow that rejection (no console spam, no thrown error) and arm one-shot
 * listeners so playback simply starts working after the first click/keypress.
 *
 * Sound is BEST-EFFORT — every call is guarded so the bell keeps working even if
 * audio is unavailable.
 */

const SRC = "/notification.wav";
const MUTED_KEY = "fleet.notifSoundMuted";
const MIN_GAP_MS = 3000;

let audio: HTMLAudioElement | null = null;
let lastPlayed = 0;
let lastCount: number | null = null;
let unlockArmed = false;
/** Set once a play() has been rejected by the autoplay policy. */
let needsGesture = false;

function el(): HTMLAudioElement | null {
  if (typeof window === "undefined") return null; // SSR guard
  if (!audio) {
    try {
      audio = new Audio(SRC);
      audio.preload = "auto";
      audio.volume = 0.5;
    } catch {
      return null;
    }
  }
  return audio;
}

/** After the first real user gesture, browsers allow playback — retry silently. */
function armUnlock() {
  if (unlockArmed || typeof window === "undefined") return;
  unlockArmed = true;
  const unlock = () => {
    needsGesture = false;
    const a = el();
    // Prime the element during the gesture so later programmatic plays are allowed.
    if (a) {
      a.muted = true;
      a.play()
        .then(() => {
          a.pause();
          a.currentTime = 0;
          a.muted = false;
        })
        .catch(() => {
          a.muted = false;
        });
    }
    window.removeEventListener("pointerdown", unlock);
    window.removeEventListener("keydown", unlock);
  };
  window.addEventListener("pointerdown", unlock, { once: true });
  window.addEventListener("keydown", unlock, { once: true });
}

export function isNotificationSoundMuted(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(MUTED_KEY) === "1";
  } catch {
    return false;
  }
}

export function setNotificationSoundMuted(muted: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(MUTED_KEY, muted ? "1" : "0");
  } catch {
    /* storage blocked (private mode) — in-memory behaviour still applies */
  }
}

/** Play the chime now, honouring mute + the burst throttle. Never throws. */
export function playNotificationSound(): void {
  if (isNotificationSoundMuted()) return;
  const now = Date.now();
  if (now - lastPlayed < MIN_GAP_MS) return; // same burst
  lastPlayed = now;
  try {
    const a = el();
    if (!a) return;
    a.currentTime = 0;
    const p = a.play();
    if (p && typeof p.catch === "function") {
      p.catch(() => {
        // Autoplay blocked (or no output device). Stay quiet and wait for a
        // gesture — never surface this as an error.
        needsGesture = true;
        armUnlock();
      });
    }
  } catch {
    /* best-effort only */
  }
}

/**
 * Feed the CURRENT unread count. Chimes only when it rose above the previous
 * value; the first call establishes the baseline silently.
 */
export function reportUnreadCount(count: number): void {
  const previous = lastCount;
  lastCount = count;
  if (previous === null) {
    armUnlock(); // get the gesture listener in place before the first arrival
    return; // baseline — never chime
  }
  if (count > previous) playNotificationSound();
}

/** Forget the baseline (e.g. on sign-out) so the next count is a fresh baseline. */
export function resetNotificationBaseline(): void {
  lastCount = null;
}

/** True when a chime was blocked and is waiting for the first user gesture. */
export function soundNeedsGesture(): boolean {
  return needsGesture;
}

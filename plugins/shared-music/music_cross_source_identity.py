"""Is the YouTube video we found actually the Spotify track we were sent?

The failure this prevents
------------------------
A Spotify link carries an exact recording: one master, one duration, one
version. Resolving it to audio means searching YouTube by *text* and picking a
winner — and text is exactly the signal that cannot tell "Song (Radio Edit)"
from "Song (Extended Club Mix)", a studio master from a live take, or an
original from a sped-up upload.

Before this module, `resolve_from_spotify` fetched `spotify_duration_s` from
the Web API and then never used it: selection was `_score_candidate` text
scoring with a `score > 0` floor. So the one hard, numeric, cross-source signal
available — Spotify gives duration to the millisecond — was being thrown away
at precisely the moment it could have caught a wrong-version match. The user
sends one song and <AGENT_NAME> analyses a different one, confidently, with no way to
notice.

The rule
--------
**A weak match is not a match.** When the evidence does not identify the same
recording, this returns a verdict that says so, and the caller degrades to
"I know the song, I could not confirm the audio" — which is honest and
recoverable — instead of analysing a remix and describing it as the track.

Signals, strongest first
------------------------
1. **Duration.** Different masters of the same recording agree within a few
   seconds; a different *version* rarely does. This is the only signal here
   that is numeric and source-independent, so it is the only one that can veto
   on its own.
2. **Version markers.** "remix", "live", "sped up", "cover", "instrumental",
   "karaoke" and friends. Asymmetric on purpose: a marker the candidate has and
   Spotify does not is evidence *against*; the reverse is too (a Spotify link
   to a live version resolving to the studio take is the same mistake mirrored).
3. **Artist agreement**, then **channel authority** (`- Topic` auto-channels and
   official uploads), which are weak positive corroboration only.

No signal here is allowed to manufacture confidence on its own — `VERIFIED`
requires duration corroboration, because everything else is text.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: Same recording, different platform masters: encoding, silence trimming and
#: fade handling differ by a second or two. Beyond this we are looking at a
#: different edit, not a different encode.
DURATION_EXACT_S = 2.0
#: Still plausibly the same recording (a track with a long fade, a single vs
#: album master), but no longer enough to confirm on its own.
DURATION_CLOSE_S = 5.0

#: Markers that denote a *different recording* of the same composition.
_VERSION_MARKERS = {
    "remix": ("remix", "리믹스"),
    "live": ("live", "라이브", "concert", "unplugged"),
    "acoustic": ("acoustic", "어쿠스틱"),
    "cover": ("cover", "커버"),
    "instrumental": ("instrumental", "inst.", "반주"),
    "karaoke": ("karaoke", "노래방", "mr "),
    "sped_up": ("sped up", "spedup", "nightcore", "speed up"),
    "slowed": ("slowed", "reverb", "slow down"),
    "extended": ("extended", "club mix", "long version"),
    "radio_edit": ("radio edit", "short version"),
    "remaster": ("remaster", "remastered", "리마스터"),
    "8d": ("8d audio", "8d "),
    "reaction": ("reaction", "리액션"),
}

#: These change the *recording*. `remaster` deliberately is not here: a
#: remaster is the same performance and the same duration, and treating it as a
#: mismatch would reject the correct answer for most catalogue music.
_DISQUALIFYING = frozenset({
    "remix", "live", "acoustic", "cover", "instrumental", "karaoke",
    "sped_up", "slowed", "extended", "8d", "reaction",
})

VERIFIED = "verified"
PLAUSIBLE = "plausible"
UNCONFIRMED = "unconfirmed"


def _normalize(text: str) -> str:
    """Casefold + NFKC so Korean and full-width text compare sanely.

    NFKC and not NFD: macOS hands us decomposed Hangul and a naive comparison
    then fails on strings that render identically. See the NFD trap in
    belief-revision.
    """
    return unicodedata.normalize("NFKC", (text or "")).casefold()


def markers_in(text: str) -> set[str]:
    norm = _normalize(text)
    found = set()
    for name, needles in _VERSION_MARKERS.items():
        if any(n in norm for n in needles):
            found.add(name)
    return found


def _artist_agrees(spotify_artist: str, candidate_text: str) -> bool:
    """Any one credited artist appearing in the candidate counts.

    Spotify credits features as separate artists ("A, B"), while YouTube often
    titles the same track "A - Song (feat. B)". Requiring all of them to appear
    would reject correct matches, so this asks for one.
    """
    norm = _normalize(candidate_text)
    parts = [p.strip() for p in re.split(r"[,&×xX]|feat\.?|ft\.?", _normalize(spotify_artist)) if p.strip()]
    return any(len(p) > 1 and p in norm for p in parts)


@dataclass
class IdentityVerdict:
    """Why we do or do not believe this is the same recording."""
    status: str
    confidence: float
    reasons: tuple[str, ...]
    duration_delta_s: float | None = None
    candidate_markers: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """Only VERIFIED and PLAUSIBLE may be analysed as the linked track."""
        return self.status in (VERIFIED, PLAUSIBLE)

    def as_dict(self) -> dict:
        return {
            "identity_status": self.status,
            "identity_confidence": round(self.confidence, 2),
            "identity_reasons": list(self.reasons),
            "duration_delta_s": self.duration_delta_s,
            "candidate_version_markers": list(self.candidate_markers),
        }


def verify(
    *,
    spotify_title: str,
    spotify_artist: str,
    spotify_duration_s: float | None,
    candidate_title: str,
    candidate_channel: str = "",
    candidate_duration_s: float | None = None,
) -> IdentityVerdict:
    """Decide whether `candidate` is the recording `spotify_*` describes."""
    reasons: list[str] = []
    text = f"{candidate_title} {candidate_channel}"
    confidence = 0.0

    # -- duration -----------------------------------------------------------
    delta = None
    if spotify_duration_s and candidate_duration_s:
        delta = abs(float(spotify_duration_s) - float(candidate_duration_s))
        if delta <= DURATION_EXACT_S:
            confidence += 0.55
            reasons.append(f"duration matches within {DURATION_EXACT_S:g}s")
        elif delta <= DURATION_CLOSE_S:
            confidence += 0.25
            reasons.append(f"duration within {DURATION_CLOSE_S:g}s but not exact")
        else:
            # A hard veto, and the only one. Everything else here is text.
            return IdentityVerdict(
                UNCONFIRMED, 0.0,
                (f"duration differs by {delta:.1f}s — different recording, not a different encode",),
                duration_delta_s=delta,
                candidate_markers=tuple(sorted(markers_in(text))),
            )
    else:
        reasons.append("no duration available on both sides")

    # -- version markers ----------------------------------------------------
    spotify_markers = markers_in(f"{spotify_title} {spotify_artist}")
    candidate_markers = markers_in(text)
    disagreeing = (spotify_markers ^ candidate_markers) & _DISQUALIFYING
    if disagreeing:
        return IdentityVerdict(
            UNCONFIRMED, 0.0,
            (f"version mismatch: {sorted(disagreeing)}",),
            duration_delta_s=delta,
            candidate_markers=tuple(sorted(candidate_markers)),
        )
    if candidate_markers & spotify_markers:
        confidence += 0.1
        reasons.append("version markers agree")

    # -- title / artist -----------------------------------------------------
    if _normalize(spotify_title) in _normalize(text):
        confidence += 0.25
        reasons.append("title present")
    else:
        reasons.append("title not literally present")
    artist_known = bool(spotify_artist)
    artist_agrees = artist_known and _artist_agrees(spotify_artist, text)
    if artist_agrees:
        confidence += 0.2
        reasons.append("artist agrees")
    elif artist_known:
        # Negative evidence, not merely a missing bonus. Duration plus a title
        # substring alone was enough to reach VERIFIED, which meant a different
        # act's recording of the same song — a cover that forgot to say so, or
        # two songs that share a name — could clear the bar on a coincidence of
        # length. When Spotify told us who made this and the candidate does not
        # mention them, that is a reason to doubt.
        confidence -= 0.2
        reasons.append("artist not found in candidate")

    # -- channel authority (corroboration only) -----------------------------
    channel = _normalize(candidate_channel)
    if channel.endswith("- topic") or " - topic" in channel:
        confidence += 0.15
        reasons.append("auto-generated Topic channel")
    elif any(k in _normalize(candidate_title) for k in ("official audio", "official video", "official mv")):
        confidence += 0.1
        reasons.append("official upload")

    # -- verdict ------------------------------------------------------------
    # VERIFIED requires duration corroboration. Text alone can be perfectly
    # convincing about the wrong recording, which is the entire problem.
    duration_corroborated = delta is not None and delta <= DURATION_EXACT_S
    if artist_known and not artist_agrees:
        # Cap below VERIFIED: whatever else lines up, we were told the artist
        # and could not find them.
        confidence = min(confidence, 0.6)
    if duration_corroborated and confidence >= 0.8:
        status = VERIFIED
    elif confidence >= 0.55:
        status = PLAUSIBLE
    else:
        status = UNCONFIRMED
    return IdentityVerdict(
        status, min(confidence, 1.0), tuple(reasons),
        duration_delta_s=delta, candidate_markers=tuple(sorted(candidate_markers)),
    )


__all__ = [
    "DURATION_CLOSE_S", "DURATION_EXACT_S", "IdentityVerdict", "PLAUSIBLE",
    "UNCONFIRMED", "VERIFIED", "markers_in", "verify",
]

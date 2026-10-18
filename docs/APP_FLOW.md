# App flow (agreed 2026-10-16)

Principle: coach, don't hand over a blank canvas. Every step shows a few generated options; the user picks
one and refines it with a sentence. Re-rolls are free (local GPU), so options at every step are the point —
the #1 complaint in the category is paying credits for bad generations.

1. **Drop in the song + lyric sheet.** Analysis runs (beat grid, word timings, sections from the sheet's tags).
   The first screen is the **animatic**: the whole song as beat-locked placeholder cards, playable with the
   audio. The user never faces an empty timeline; everything after this fills cards in.
1b. **Take vs sheet.** Real takes drift from the lyric sheet (Suno ad-libs: repeated hooks, dropped lines, echoes). A line-level diff of the sheet against what Whisper heard proposes the corrected sheet for THIS take ("repeated hook at 0:13, dropped line in the bridge, 'Delivered' x3: accept?"). The take is the truth; cuts are anchored to sung words so they survive re-records.
2. **Story.** Per section, 2-3 suggested beats drawn from the lyrics; pick or rewrite. A full written
   treatment can be pasted instead ("I know what I want" path).
3. **Character.** Four directions -> pick -> refine (face, accessories) -> lock a character sheet
   (views + expressions). The sheet is the reference for every shot.
4. **World / style.** Same pick-and-refine; becomes a style constant every shot inherits.
5. **Shots.** Each card offers three keyframes (character placed from the sheet); pick, then render the clip
   (audio-to-video on that shot's exact slice). Cards update in place: card -> keyframe -> clip.
6. **Review.** Watch the cut, re-roll any single card, export.

**Foley / transitions.** The lyric sheet's stage directions ("(Beat drops)", "(Riser builds, snare roll)",
"(Phone lock click)") become timed cues automatically; lyric moments can be added as cues. Each cue gets 2-3 open-weight
text-to-audio takes (LTX-2.5 T2A, no extra models) to pick by ear. Conform places one-shots on their word/beat and times
transitions to END on the next section's bar line. Stretch: video-to-audio (e.g. MMAudio) for picture-locked hits, after a
weights-license check.

**Levers.** Creative choices are picks; intensities are dials: reactive-layer strength per section, foley level,
transition intensity, music duck under hits.

Deterministic rig underneath: planner (shots on bar lines), conform (frame-exact cut over the original song),
text overlays timed to words, a **reactive layer** in the conform (deterministic, from the analysis: punch-in on downbeats, exposure/bloom on the kick or bass envelope, sparse flashes on snares, word-timed freezes; turns "LTX roughly follows the song" into "every downbeat visibly lands"; per-section intensity is a creative choice), compliance check (no partner/API nodes in any graph), per-shot hashing for re-rolls.

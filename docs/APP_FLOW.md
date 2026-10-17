# App flow (agreed 2026-10-16)

Principle: coach, don't hand over a blank canvas. Every step shows a few generated options; the user picks
one and refines it with a sentence. Re-rolls are free (local GPU), so options at every step are the point —
the #1 complaint in the category is paying credits for bad generations.

1. **Drop in the song + lyric sheet.** Analysis runs (beat grid, word timings, sections from the sheet's tags).
   The first screen is the **animatic**: the whole song as beat-locked placeholder cards, playable with the
   audio. The user never faces an empty timeline; everything after this fills cards in.
2. **Story.** Per section, 2-3 suggested beats drawn from the lyrics; pick or rewrite. A full written
   treatment can be pasted instead ("I know what I want" path).
3. **Character.** Four directions -> pick -> refine (face, accessories) -> lock a character sheet
   (views + expressions). The sheet is the reference for every shot.
4. **World / style.** Same pick-and-refine; becomes a style constant every shot inherits.
5. **Shots.** Each card offers three keyframes (character placed from the sheet); pick, then render the clip
   (audio-to-video on that shot's exact slice). Cards update in place: card -> keyframe -> clip.
6. **Review.** Watch the cut, re-roll any single card, export.

Deterministic rig underneath: planner (shots on bar lines), conform (frame-exact cut over the original song),
text overlays timed to words, a **reactive layer** in the conform (deterministic, from the analysis: punch-in on downbeats, exposure/bloom on the kick or bass envelope, sparse flashes on snares, word-timed freezes; turns "LTX roughly follows the song" into "every downbeat visibly lands"; per-section intensity is a creative choice), compliance check (no partner/API nodes in any graph), per-shot hashing for re-rolls.

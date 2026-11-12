"""The episode page add-on: word timing, time-placed references and a synced transcript page
for a finished chapter. Its own entry point: `python -m episode <timing|references|build> <run>`.

It reads a run folder and writes only into <run>/11_episode/. It reuses the pipeline's helpers
(stages.common, stages.tts, stages.llm) read-only and edits none of them; see INTEGRATION.md.
"""

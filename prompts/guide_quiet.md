# Lines for when the guide says nothing after a tool (a fragment)

Used by stages/guide.py when a turn's tools ran but the model's reply after them
was empty, so the listener still hears what happened. One "key: line" per line.
The started_fast line is for a chapter made the fast way (it states no time); the
listening line is for a turn whose only tool was the silent scout_topic.
---
started: It's under way. It takes about {minutes} minutes; I'll let you know when it's ready.
noted: Noted.
done: Done.
started_fast: It's under way. The page will tell you when it can start playing.
listening: Go on, I'm listening.

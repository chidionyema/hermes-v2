# Telegram voice message transcription demo

When the founder sends a voice note on Telegram, it is downloaded, transcribed locally using faster-whisper, and the transcript becomes the message text. The transcript is captured to his records like any typed message.

```
User sends voice note (5 seconds of speech)
↓
Gateway downloads OGG file
↓
Faster-whisper transcribes locally (~1.5 MB model)
↓
Transcript becomes event.text
↓
Founder sees: "The quick brown fox jumps over the lazy dog"
↓
Message is recorded in his session transcript
```

Approval buttons (Approve / Deny) are inline on every message that asks for a decision. Click a button to record the word without typing.

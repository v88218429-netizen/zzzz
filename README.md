# VK Video Transcriber

Technical automation used to turn public video links into Markdown transcripts.

Flow: request file -> yt-dlp -> subtitles when available -> local Whisper fallback -> Markdown artifact.

# Video Transcriber

Automation for turning public video links into Markdown transcripts.

Supported input modes:

## 1. One video

```json
{
  "url": "https://www.youtube.com/watch?v=VIDEO_ID"
}
```

VK, YouTube, Rutube and other public URLs supported by yt-dlp can use the same format.

## 2. Batch of links

```json
{
  "urls": [
    "https://vkvideo.ru/video-123_456",
    "https://www.youtube.com/watch?v=VIDEO_ID",
    "https://youtu.be/VIDEO_ID"
  ]
}
```

Each video gets its own Markdown transcript plus one collection summary.

## 3. YouTube search

```json
{
  "search": "Wildberries FBS",
  "limit": 15
}
```

The worker asks yt-dlp for the first N YouTube search results and transcribes them one by one.

Multiple searches are also supported:

```json
{
  "searches": [
    {"query": "Wildberries FBS", "limit": 15},
    {"query": "Wildberries реклама", "limit": 15}
  ]
}
```

## Processing flow

request JSON -> expand URLs/search -> subtitles/automatic subtitles first -> Whisper fallback only when subtitles are unavailable -> Markdown transcripts -> collection summary -> processed marker.

Already processed request files are skipped on later runs, so adding a new request does not retranscribe the old library.

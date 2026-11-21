# Understanding Claude's Usage Limits — $20 Pro vs $100 Max plan

- Source: https://www.youtube.com/watch?v=2fbqo9Bdd_w
- Video ID: 2fbqo9Bdd_w
- Task ID: TA_API_2026
- Content type: video
- Author/channel: Greg
- Duration: 6:01
- Discovery query: WB Excel/API лимиты
- Transcript method: FreeTranscriptAPI
- Generated: 2026-11-21T16:13:09.127878+00:00

## Transcript

[00:00] A lot of people, me included, have found
[00:02] the way Claude calculates their usage
[00:04] limits to be a bit confusing. So, I
[00:07] wanted to create a quick video to
[00:09] clarify and share my thoughts on how it
[00:11] all works. Claude works a bit different
[00:13] than other providers like ChatGPT, where
[00:16] you get something like 100 messages per
[00:19] week with model XYZ. With Claude, the
[00:22] limits aren't just based on message
[00:23] count. They're also based on sessions.
[00:26] So, what is a session? It's essentially
[00:28] a 5-hour block of time that begins the
[00:31] moment you send your first message to
[00:33] Claude. So, with both the $100 and the
[00:36] $200 max plans, you get 50 sessions per
[00:40] month. And if you exceed that, Claude
[00:42] says they could limit your access. And
[00:44] on the $20 pro plan, it doesn't
[00:46] specifically specify how many sessions
[00:49] you have. So, for now, it looks like
[00:51] it's unlimited, though that can always
[00:53] change. Let's break this down with an
[00:55] example. Say you sign up for the max
[00:57] plan today. You'll log into Claude code
[01:00] at 9:00 a.m. and send your first
[01:02] message. That starts session number one.
[01:04] From 9:00 a.m. to 2:00 p.m., everything
[01:07] you do, whether you're actively coding
[01:10] or just idle, it all gets counted as
[01:12] part of that one 5-hour session. Now,
[01:15] depending on your plan, here's what your
[01:18] usage limits looks like. On the $20 pro
[01:20] plan, you get around 45 messages per
[01:23] session. On the $100 max plan, you get
[01:26] about 225 messages. And on the $200 max
[01:30] plan, you get around 900 messages. Now,
[01:34] here's where things get a little tricky.
[01:36] The moment 2:00 p.m. hits, the first
[01:38] session ends. If you send a message at
[01:41] 2:01 p.m., even something simple like a
[01:44] follow-up question or a thank you, you
[01:46] have automatically started session
[01:48] number two. That session number two now
[01:51] runs from 2:01 p.m. to 7:01 p.m. with
[01:55] all your usage limits resetted. And
[01:58] here's the frustrating part. Maybe you
[02:00] didn't intend to start that new session.
[02:02] Maybe you were wrapping things up at
[02:03] 2:00 p.m. and you just want to say thank
[02:05] you to Claude for all its help. Well, if
[02:08] that message gets sent out at 2:01 p.m.,
[02:11] you have now started an entirely new
[02:13] session, even though you didn't mean to.
[02:15] That single message starts session
[02:18] number two. So now you're down to 48
[02:20] sessions. And that is really annoying.
[02:23] Most people won't be tracking when they
[02:25] started a session and how much time
[02:27] remains in their session. They shouldn't
[02:29] have to, right? That it's not practical.
[02:31] So my question to Anthropic is, if a
[02:34] user accidentally starts a new session,
[02:36] say by sending a few messages right
[02:38] after the previous session ends, should
[02:40] that really count as a full session? It
[02:43] feels like there has to be some type of
[02:44] back-end logic to handle cases like
[02:47] this. Maybe Claude actually allows for
[02:49] 50 full-length sessions, and those short
[02:52] one-off sessions don't get counted the
[02:54] same way. I feel I might be overly
[02:56] optimistic here, but to me, that just
[02:58] feels like a more fair and user-friendly
[03:01] approach. That said, since the Max plan
[03:04] has only been around for a few weeks,
[03:06] there just isn't any clear data or
[03:08] official words from Claude on how it
[03:10] handles situations like this. I totally
[03:12] understand where Anthropic is coming
[03:14] from. They definitely implemented this
[03:16] 50 sessions per month cap to limit
[03:19] potential abuse. Let's take a look at a
[03:21] extreme example. So assume we're on the
[03:24] 100 on Max plan, and remember, on this
[03:26] plan we get 225 messages per session. So
[03:30] going with this example, this is going
[03:32] to be about five sessions per day,
[03:34] because that's about 24-25 hours per
[03:37] day. So five sessions per day, this
[03:39] times 30 days in one month, this is
[03:41] going to get you about 150 sessions per
[03:44] month. Now, 150 sessions per month times
[03:48] 225 messages, this is going to get you
[03:51] about 33,750
[03:54] messages per month. And assuming a
[03:56] message query costs 25 cents, this is
[03:59] going to cost Claude $8,500,
[04:03] compared to your $100 that you pay for
[04:05] this plan. Now, if we assume a message
[04:07] query costs about 50 cents, this is
[04:09] going to cost Claude around $17,000
[04:12] versus your $100. So, yeah, I can see
[04:15] why Claude might not be too happy. So,
[04:17] here's my thought on this. If you're
[04:19] able to fully utilize those 5-hour
[04:22] blocks where you're actively coding and
[04:24] collaborating with Claude the entire
[04:26] time, then yes, I think 50 sessions per
[04:29] month is more than fair. But obviously,
[04:31] this means you now have to be strategic
[04:33] in how you use Claude. You no longer can
[04:36] just randomly chat with Claude
[04:37] throughout the day, because if you say
[04:39] send a message at 8:00 a.m. and then
[04:41] send another one at 3:00 p.m. and then
[04:44] send another one at 9:00 p.m., you have
[04:46] now used three sessions. And that's
[04:48] frustrating, because people naturally
[04:50] want to interact with Claude when they
[04:52] need it, not according to a rigid
[04:54] schedule. Having to plan your usage just
[04:57] to avoid wasting sessions, it feels very
[05:00] counterintuitive, especially for a tool
[05:02] that's meant to be helpful and
[05:03] accessible. It It just doesn't make
[05:05] sense to me. From what I can see, the
[05:07] way the Max plan is currently structured
[05:09] with its 50 sessions per month limit,
[05:12] it's really designed for developers or
[05:14] power users who can carve out
[05:16] uninterrupted 5-hour blocks to do
[05:18] focused work. If that's what you're
[05:20] trying to go for, the Max plan could be
[05:22] a great fit. And obviously, the Max plan
[05:25] has features that's not available on the
[05:27] Pro plan, such as research and now
[05:29] Claude Code. So, if you want to try
[05:31] those features, then yeah, you have to
[05:33] buy the Max plan. But if your usage is
[05:35] more casual, maybe you prefer chatting
[05:37] with Claude throughout the day, then the
[05:39] Max plan probably isn't the best fit. In
[05:42] that case, the $20 Pro plan might
[05:44] actually to better. Even though it comes
[05:46] with a lower message cap, it supposedly
[05:49] has unlimited sessions per month, so it
[05:51] gives you more flexibility to chat with
[05:52] Claude whenever you want without having
[05:54] to worry about burning a session. Let me
[05:57] know if this was helpful and if anyone
[05:59] has any questions.

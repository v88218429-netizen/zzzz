# Claude Just Solved Session Limits

- Source: https://www.youtube.com/watch?v=3QclAjmu5Tw
- Video ID: 3QclAjmu5Tw
- Task ID: TA_API_2026
- Content type: video
- Author/channel: Nate Herk | AI Automation
- Duration: 10:22
- Discovery query: WB Excel/API лимиты
- Transcript method: FreeTranscriptAPI
- Generated: 2026-11-26T00:18:41.370526+00:00

## Transcript

[00:00] So, today Claude announced that they
[00:01] agreed to a partnership with SpaceX, so
[00:03] Elon Musk's company, and it's going to
[00:05] substantially increase their compute
[00:06] capacity, which means that they've been
[00:08] able to increase their usage limits for
[00:10] Claude Code and for the Claude API. So,
[00:12] today in San Francisco was the first
[00:14] Code with Claude event of 2026, which is
[00:16] basically just a big developer
[00:18] conference that they're doing in San
[00:19] Francisco, London, and Tokyo over the
[00:21] next month or so. And they actually got
[00:23] so much demand for this that they
[00:24] extended it. It looks like they did an
[00:26] extra day in each of these locations.
[00:28] So, pretty cool. But as you guys
[00:29] probably are aware if you've been using
[00:30] Claude for a while is that this past
[00:33] couple months, this past quarter, has
[00:34] been awful with outages. There's been so
[00:37] many times where Claude has just died
[00:39] while you're trying to use it. And
[00:40] there's probably a lot of different
[00:41] reasons for that, you know, like they
[00:42] were doing a lot of testing, they were
[00:43] shipping so many features, they had the
[00:44] Opus, they had Mythos. But really the
[00:46] main reason is that they just didn't
[00:47] have enough compute to handle how much
[00:49] demand there was. There was way too many
[00:51] people trying to use Claude than what
[00:53] Claude could actually support. So, today
[00:55] I wanted to actually break this down and
[00:56] help you guys understand what this means
[00:58] for you practically and what you might
[00:59] want to start doing differently. So,
[01:01] higher usage limits for Claude and a
[01:02] compute deal with SpaceX, very very
[01:05] interesting. So, because of this
[01:07] partnership, what that means, effective
[01:09] immediately, is that first of all,
[01:11] they're going to be able to double
[01:12] Claude Code's 5-hour rate limits,
[01:14] double. Whether you're on Pro, Max, or
[01:16] Team, your 5-hour limit is going to be
[01:19] doubled. So, when you're here inside of
[01:20] Claude, whatever plan you're on, this is
[01:22] going to last two times as long now.
[01:25] What they also did is they removed the
[01:26] peak hours limit reduction on Claude
[01:28] Code for Pro and Max accounts. So, if
[01:31] you guys remember, maybe a month ago or
[01:32] so, they came out with this announcement
[01:34] and said, "Hey, during peak hours, so
[01:35] like weekday mornings-ish, you will hit
[01:37] your session limit faster." Because once
[01:39] again, they had way too many people
[01:40] using Claude Code during peak hours, and
[01:43] they didn't have enough compute on their
[01:44] side. And then they also did that thing
[01:46] where they tested out not letting people
[01:48] buy a Pro plan, so the $20-a-month plan,
[01:51] and using Claude Code. You had to buy a
[01:53] Max plan in order to use it, unless you
[01:55] who already a subscriber.
[01:56] So, they were doing all these things to
[01:57] figure out like how can we actually
[01:58] compete or how can we actually keep up
[02:00] with how much demand we're getting for
[02:02] our platform right now? Because you also
[02:04] have the issue of if you go out and buy
[02:06] a bunch of extra compute and it's just
[02:07] sitting there and it's not being
[02:09] utilized, then that's also money going
[02:10] down the drain because the computer is
[02:12] just sitting there not being utilized.
[02:14] So, if you really think about like the
[02:15] math and the projections you'd have to
[02:17] do, it's really not a very simple
[02:19] problem to solve. And I'm not sure if
[02:21] this was the intention. Remember when
[02:22] people were using the Claude
[02:23] subscription for like open Claude and
[02:25] Hermes agent and then they said, "Hey,
[02:26] you can't do that anymore. It's against
[02:27] our terms of service." Yes, it is
[02:29] against their terms of service, but I
[02:31] wonder if there was also some motivation
[02:32] there to be like, "Whoa, we got to like
[02:34] chill a little bit with how many people
[02:35] are just abusing their subscriptions
[02:37] right now." And we know if we switch
[02:38] over to API keys, then a lot of people
[02:40] might stop using our models as much for
[02:42] open Claude or Hermes. So, anyways, just
[02:44] a quick thought. But then the final
[02:46] thing is that they are raising their API
[02:47] rate limits considerably for Claude Opus
[02:50] models. So, it is a really decent chunk.
[02:53] So, per minute, you used to only be able
[02:55] to send 30K input tokens at a time or
[02:57] you'd be rate limited. And that has been
[02:59] upgraded by like 16% on the output side.
[03:01] It used to be 8,000 a minute and now
[03:03] it's 80,000 a minute. So, every single
[03:05] tier got a really a significant jump
[03:07] here. I mean, if you think about only
[03:09] being able to output 8,000 tokens in a
[03:11] minute, I have gone past that so many
[03:13] times. Once again, this is not for
[03:15] Claude code, this is for the API. But
[03:17] besides just the partnership with
[03:18] SpaceX, they were also on kind of a
[03:20] buying spree. You know, they have an
[03:21] agreement with Amazon. They have an
[03:23] agreement with Google and Broadcom. They
[03:24] also have a partnership with Microsoft
[03:25] and Nvidia and a big investment in
[03:27] American AI infrastructure with
[03:29] FluidStack. So, this was obviously a big
[03:31] announcement with SpaceX, but they were
[03:33] kind of working towards this and they've
[03:34] been moving in the direction of figuring
[03:35] out how to get more compute either way.
[03:37] And the day before this actual
[03:39] conference, they made an announcement
[03:40] about partnering with that Goldman Sachs
[03:42] JV as well as Blackstone. And you can
[03:46] just tell that they're really going
[03:47] after enterprise here and they need to
[03:49] have compute to be able to handle
[03:50] enterprise and they are expanding
[03:52] internationally. So, anyways, let's just
[03:53] start to break this down. I don't want
[03:54] to waste too much time. I want to keep
[03:55] this video pretty quick. So, the
[03:57] headline, we read all this, right?
[03:58] Anthropic released three big changes. Um
[04:00] they also released something pretty
[04:02] interesting with managed agents. They
[04:03] gave them like webhooks and auto
[04:05] dreaming and um you know, multi-agent
[04:07] orchestration. And I'm not going to
[04:08] cover that right now. I'm definitely
[04:09] going to play around with it and I'll
[04:11] bring a video if I find some interesting
[04:12] stuff. But, this one's more about the
[04:14] actual usage limits.
[04:15] The 5-hour rate limits got doubled. Peak
[04:17] hours throttling has been removed. And
[04:19] API rate limits have been
[04:22] improved significantly. So, why does
[04:24] this matter?
[04:25] Well, for months, everyone who was
[04:26] building with Claude code has been
[04:28] hitting walls. There's been so many
[04:29] complaints about people hitting the
[04:30] limits so fast. So many complaints about
[04:32] people wanting to upgrade from Pro to
[04:34] Max to Max higher, and even on the
[04:37] highest Max plan still getting shut down
[04:39] and not being able to use 5 hours worth
[04:41] of Claude code. And I don't know how
[04:42] many of you guys are, but if you were
[04:44] using the API for Opus, and you were
[04:46] trying to build production agents or you
[04:47] know, apps that have some AI on the back
[04:49] end with Opus, you might be hitting rate
[04:51] limits very frequently. So, these are
[04:53] the three changes that we just talked
[04:54] about that have happened today. And
[04:56] let's look at this rate limiting again
[04:58] because the statistics are pretty
[04:59] interesting. The lowest tiers obviously
[05:01] got the biggest multiples. We see 16
[05:03] here and we see 10 here. But still,
[05:05] having all of these other ones, you
[05:06] know, the inputs getting more than the
[05:07] output just because input is much less
[05:10] expensive than output tokens. But this
[05:12] basically meant if you had half a
[05:13] million input tokens per minute, you
[05:15] could pump roughly 370 pages of context
[05:17] per minute. And that's just on tier one.
[05:19] But before today, you would have only
[05:20] had 30K tokens, which might have been
[05:22] like 20 to 22 pages. And on the output
[05:24] side, we can now generate way more
[05:26] content much quicker. So, if you wanted
[05:28] to have like a bunch of different agents
[05:29] running in parallel, that just would
[05:31] have been really, really hard to do
[05:32] under the previous rate limits with
[05:34] Opus. And so, obviously how they paid
[05:36] for this was the SpaceX deal, right?
[05:37] They got 300 megawatts of capacity. They
[05:40] got over 220,000 Nvidia GPUs. And they
[05:43] did this all super, super fast, which is
[05:45] really impressive. And I'm not going to
[05:46] get very technical here, right? But
[05:47] compute is expensive and these AI models
[05:50] need compute. If you think about like
[05:52] you have all these closed source models
[05:53] like the Geminis and the Opuses and the
[05:55] GPTs, and if you think about the open
[05:57] source models that we have like the deep
[05:58] six and the Gemmas and all these other
[06:00] open source models, what's the
[06:01] difference? Why are the closed source
[06:03] ones so much better? It's because they
[06:04] have more compute behind them. The open
[06:06] source models that you take and you want
[06:07] to run locally, you have to have a
[06:09] machine with enough RAM and enough VRAM
[06:11] to actually run them and run them fast
[06:13] and run them well. But when you're
[06:14] actually just like using cloud code and
[06:16] you're talking to Opus, you're relying
[06:17] on Anthropic's servers. You're relying
[06:19] on Anthropic's infrastructure to
[06:20] actually process that for you and that's
[06:21] why it's able to come back so fast. And
[06:23] even if you wanted to like buy a VPS to
[06:25] host a local model on, you would have to
[06:26] buy a VPS that has enough compute to
[06:29] actually hold and run all of those
[06:31] models. So once again, Amazon, Google,
[06:33] Broadcom, Microsoft, Nvidia, and
[06:34] Fluidstack, they're clearly investing
[06:36] here in compute. And what's interesting
[06:38] here, which was at the bottom of the
[06:39] announcement, is that Anthropic and
[06:41] SpaceX have expressed interest in
[06:42] developing multiple gigawatts of orbital
[06:45] AI compute capacity. Which means GPUs in
[06:48] space. Right here. Super interesting. As
[06:51] part of this agreement, we have also
[06:52] expressed interest in partnering with
[06:53] SpaceX to develop multiple gigawatts of
[06:56] orbital AI compute capacity. So cool. So
[06:59] it doesn't seem like that's going to
[07:00] happen right now in this year, but
[07:02] Anthropic believes that terrestrial
[07:04] compute, which is stuff like the power
[07:05] and the water and the cooling and also
[07:07] the community not liking how much water
[07:09] and electricity it's using, has a real
[07:11] long-term ceiling. So putting compute in
[07:13] space and think all these AI models
[07:15] running in space is what's going to
[07:18] really unlock like the next level of all
[07:20] of these different AI models. So what
[07:22] does this change for you guys, for
[07:23] builders?
[07:25] Um I came up with these five main points
[07:26] and not all five may hit you, but let's
[07:29] just run through them quick. The first
[07:30] thing is to retest workflows that broke
[07:32] before. So if you tried building an Opus
[07:33] agent six months ago and you gave up
[07:35] because of rate limits, the wall might
[07:36] not actually exist anymore and it's
[07:38] worth a reattempt. I always think of
[07:40] this story when I was working with a
[07:41] client and they wanted a LinkedIn
[07:42] automation with AI generated
[07:44] infographics and I was like I just I
[07:46] can't confidently give you something
[07:47] that I would want you to post. Like it's
[07:48] just not there yet. 3 months later, new
[07:50] image model dropped. I tested it out, it
[07:52] worked. I called him up and then we
[07:54] built it out for him. So, things move
[07:55] fast. Sometimes it's worth revisiting
[07:57] old projects. The other thing is, if you
[07:59] were using something like /opus plan or
[08:01] if you were consistently delegating a
[08:03] lot of work to Haiku or Sonnet just
[08:05] because you were trying to really
[08:06] maximize your
[08:07] session limit, then maybe you can start
[08:09] to you know, give yourself you know,
[08:11] treat yourself a little bit more. Use
[08:12] Opus a little bit more.
[08:14] Now, obviously, context management is
[08:15] still going to be really, really
[08:16] important. So, I've made a ton of videos
[08:18] on that kind of stuff. You can check out
[08:19] my channel. But, with these new limits,
[08:21] you can experiment maybe a little bit
[08:22] more. Third, we have that the 1 million
[08:23] context window is finally usable in
[08:25] production because you're not going to
[08:27] be getting rate limited. And this is
[08:28] when I'm thinking of like the API calls,
[08:30] not so much the Claude code. Claude code
[08:32] can also sit behind production
[08:33] infrastructure now, not just prototypes
[08:35] because previously, if you would have
[08:36] had all of these different routines
[08:37] firing off and you still wanted to be
[08:39] able to do your knowledge work and build
[08:41] automations and stuff like that on the
[08:42] day-to-day, then you're using your
[08:44] session limit on Claude code automations
[08:46] in the agentic loop as well as your
[08:48] daily knowledge work. And you were just
[08:49] going to eat through it too quick. But
[08:50] now, with double the usage, you might be
[08:53] able to push some of those workflows
[08:55] that you want on a routine and you want
[08:56] to have like a lot of autonomy,
[08:58] those can now be routines and it doesn't
[09:00] affect your session limit as much. And
[09:02] then, multi-agent workflows are way more
[09:03] viable. So, you could have things like
[09:06] five sub-agents, each reading 50k
[09:08] tokens. And so, this one's kind of
[09:09] similar to point three, but basically
[09:10] just the idea that your API driven
[09:12] workflows,
[09:14] you have a lot more flexibility now. So
[09:15] anyways, just wanted to wrap up here
[09:16] with what it signals about Anthropic's
[09:18] direction, at least what I think. So,
[09:20] they are clearly playing for 5 plus
[09:21] years of compute. They invested a ton of
[09:24] money into it. So, obviously, this is
[09:26] the future for them. Claude code also
[09:27] seems to be clearly their flagship
[09:29] product. They didn't make any
[09:30] announcements today, at least best of my
[09:32] knowledge, about co-work. They talked
[09:33] about this Claude session limits and
[09:35] they talked about APIs second. But
[09:37] obviously this session limit counts
[09:38] towards pretty much all of your Claude
[09:40] products, but Claude code was what was
[09:43] heavily discussed. Now yes, this event
[09:45] was called code with Claude, so there's
[09:47] a bit of that, but anyways, just
[09:49] something I wanted to call out. And then
[09:50] the commitment to cover consumer
[09:52] electricity hikes around their data
[09:53] centers is a community trust play long
[09:56] term that lets them build faster than
[09:57] competitors who get pushed out of small
[09:59] towns. So anyways, I hope that this one
[10:01] was exciting. I hope that this gave you
[10:02] a few things that you want to try and
[10:03] hopefully you learned something new.
[10:05] Next I would still say it's important to
[10:06] obviously be thinking about how do
[10:09] tokens work under the hood and how
[10:10] should you be looking to manage your
[10:11] session limits. So if you want to check
[10:12] out some hacks, I'll tag this video
[10:14] right up here which I recommend you guys
[10:15] go watch next. Hopefully I'll see you
[10:16] guys over there, but as always, thanks
[10:18] for making it to the end of the video
[10:19] and I'll see you on the next one. Thanks
[10:21] everyone.

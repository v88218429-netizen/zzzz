# I Built an AI Agent for Facebook Marketplace

Source: https://www.youtube.com/watch?v=IXLty4a00z0
Transcript method: FreeTranscriptAPI

## Transcript

[0:00] All right, we got a fun one. So, today
[0:02] we're going to work on a fully automated
[0:05] Facebook Marketplace sourcing and
[0:08] selling uh app. Basically, you just
[0:10] download it fully free, open source. I'm
[0:12] just kind of giving it out uh based on
[0:15] my experience with Gizmo Grind. So, I
[0:17] started off with Gizmo Grind. That's how
[0:18] that was my first business and it was
[0:20] buying and selling phones. Honestly, I
[0:22] got through college that way. I paid
[0:24] through college that way. Now, the cool
[0:26] thing is I wish I had AI at that time.
[0:28] Now, at that time, it was like
[0:32] everything was manual. So, we'd have to
[0:34] text, we'd have to message uh every
[0:37] single buyer, every single seller to try
[0:39] and get the a good deal. And then we
[0:42] would do that whole arbitrage thing
[0:43] where we're buying low and selling high
[0:45] where we can. Now, this app fully
[0:48] automates the whole process. You just
[0:50] put in your details and you're going to
[0:52] basically get notifications when an item
[0:55] is fully negotiated by the AI. It's it's
[0:58] going to source the products for you.
[1:00] Any product, it's not going to be just
[1:01] phones. You just put in the keyword and
[1:03] I would recommend start with something
[1:04] that you understand. Uh so that way
[1:06] you're going to have less kind of ramp
[1:08] up time learning if it's phones can be
[1:10] technical. So you don't have to start
[1:12] with phones. It could be furniture if
[1:13] that's what you you're good at. Uh and
[1:16] there's so many kind of settings you can
[1:17] put into the app so that you can kind of
[1:19] narrow its focus. So let's say for
[1:20] example, uh travel time or the margin
[1:23] that you're going for. uh you don't want
[1:25] to travel. You set a low distance uh for
[1:28] the pickup of the item and if that item
[1:31] fails, it's just not going to show you
[1:34] the notification that it's ready. Again,
[1:36] the cool thing is fully open source. The
[1:38] link is going to be below so you can
[1:39] download it, make it better. Everything
[1:41] that folks are working on right now with
[1:44] AI is so early stage is you can take it,
[1:47] put it inside your harness, negotiate
[1:48] with cloud code to make it better, to
[1:51] make it tailored to you and your
[1:53] workflow. But out of the box, it's doing
[1:56] a pretty good job as is. So, uh, I want
[1:59] to jump into first how you can get it
[2:01] download. So, again, the link is going
[2:02] to be below. You, uh, download the link.
[2:05] It's, uh, going to take you to a repo.
[2:08] Okay. So, first things first, you're
[2:11] going to, uh, get a link to this repo.
[2:13] Uh, once you download this repo, uh, to
[2:16] your local harness, or really, you can
[2:17] just give this link. you can copy the
[2:20] link uh the URL and you can just give it
[2:23] to your harness and just say hey read
[2:25] the repo completely and install this for
[2:28] me locally. Now um the app right now I'm
[2:32] working to get it published on uh Chrome
[2:34] because there's an extension but for now
[2:36] inside the repo you have a developer
[2:39] download. So you basically can just
[2:41] install the app as a developer locally
[2:43] and so that looks something like this.
[2:45] So, from here, you're going to click
[2:46] load unpacked,
[2:49] and then you're just going to find that
[2:51] extension specifically. So far, it's
[2:54] just for Mac. We're working to get it
[2:55] for PC, but since it's open source,
[2:58] anybody can take this and modify it to
[3:00] work for PC. Once you find the file
[3:02] path, you're just going to click select.
[3:04] You're going to have this little
[3:05] extension buddy thing that's going to be
[3:08] your worker. It's going to connect your
[3:10] apps to Chrome.
[3:13] Now, this system doesn't use any API.
[3:17] That's the cool thing. Like, it's if you
[3:19] have a cloud code subscription, if you
[3:21] have uh chatjbt subscription, it's going
[3:24] to work with that. So, you're you're not
[3:25] paying anything extra. Once you have
[3:27] this installed and you have the app
[3:28] installed uh from your harness
[3:31] installed, the repo for you, you're
[3:33] going to see this. Now, this is the
[3:37] setup wizard. So you just basically it's
[3:39] going to ask you a few questions and so
[3:41] we can go through that right now. Now um
[3:45] again the cool thing is is work with the
[3:48] items that you know. So if you already
[3:50] understand
[3:53] phones go with iPhone. So you can just I
[3:55] would just go iPhone. Uh if you know uh
[3:58] furniture, you could go with
[4:02] furniture
[4:04] or be specific because furniture is
[4:06] going to be huge. So you could go
[4:09] antique furniture, whatever you know,
[4:13] whatever you know. But let's say for
[4:14] example, I go with iPhone.
[4:18] Continue. Uh, now this is also important
[4:22] because this is going to affect how many
[4:24] items the bot sources for you. So if you
[4:27] set a tight radius and you don't want to
[4:30] really travel, of course that means
[4:31] you're going to get fewer negotiated
[4:34] results. Like it's so remember this
[4:35] thing is fully negotiating for you,
[4:37] fully finding the item and then
[4:39] arranging the pickup, arranging all of
[4:41] that for you. So we're putting in the
[4:43] parameters.
[4:45] A smaller distance means a few results,
[4:48] but it means that you can spend less
[4:51] time traveling. You know, especially I
[4:53] live in Toronto, the traffic here is
[4:55] nuts. You know, for folks in big cities,
[4:59] this is going to matter a lot. And for
[5:01] folks who have full-time work and
[5:03] they're just looking for a side gig, uh,
[5:05] you know, you definitely want a tight
[5:06] radius here. And you might be okay with
[5:08] giving up a lot of the kind of not
[5:11] getting so many results, but focusing on
[5:14] the results that are going to mean you
[5:16] spend less time on this. Okay. So, let's
[5:19] say I put in the pickup point. So, I'll
[5:21] say let's just put Young and Dundas.
[5:25] That's the kind of our Toronto's Dundas
[5:28] Square equivalent. Uh then we're going
[5:31] to go let's say 5 km. Uh and then now
[5:34] here you get the option of either
[5:35] passing automatically or uh you're going
[5:38] to review if the item is beyond that
[5:42] threshold of distance. Right? So do you
[5:44] want to automatically just have the
[5:45] negotiator pass on that or do you want
[5:48] it to surface it to you so you can kind
[5:51] of decide if it's a really good deal?
[5:53] You'll go through the options. Uh but if
[5:55] you're really time crunch, pass
[5:57] automatically would be a good way to
[5:59] save time. Okay. Now, for the money part
[6:02] of this, all put in your total um
[6:07] single item cost. So, you don't want to
[6:09] spend more than let's say uh $300 per
[6:12] item. If you're doing phones, uh 300 is
[6:15] low and uh you're probably looking at
[6:17] more like $500 on the low end for
[6:20] something like phones, electronics that
[6:22] have good margins, right? Oftent times I
[6:25] find that the higher the price here,
[6:26] obviously you're you have more margin to
[6:29] make. not as a percentage but as a total
[6:32] dollar figure which counts in this game.
[6:36] Then on the uh total deal, let's say for
[6:40] example your budget, you don't have more
[6:41] than let's say uh a,000 to spend.
[6:44] [snorts] It's going to keep your budget.
[6:46] It's going to keep track of that for you
[6:47] so you don't overspend or overshoot or
[6:49] it doesn't negotiate once you have uh
[6:52] spent more than that. So it's tracking
[6:53] how much inventory you have for you.
[6:56] Then the minimum profit of course uh
[6:58] that you're looking for per item and the
[7:00] minimum return. So we we'll just leave
[7:02] that as is for now. But find and contact
[7:04] sellers. This is the fully automated
[7:06] version of this app. So it's going to
[7:08] negotiate for you and it's going to find
[7:10] the products for you both on the selling
[7:12] side and on the buying side. So you know
[7:15] we'll take a look at that in a second.
[7:16] All right. So now um let's say I'm going
[7:18] with Codex. So I'll connect Codex. You
[7:21] can connect Claude if you want to. Um,
[7:24] but I'm going to go C codeex.
[7:27] Okay. Uh, and then now, uh, the
[7:30] installation of the companion. So, I
[7:32] showed you earlier how to load unpack
[7:35] the, uh, extension. Soon, it's going to
[7:37] be something you can just search for in
[7:39] the marketplace, but we're just waiting
[7:40] for that to go live and get approved.
[7:42] Uh, verify everything. Um, so let's go
[7:46] ahead and check connection. Let's make
[7:48] sure this works. And it just it knows
[7:50] that you already have codec. So, boom.
[7:53] Everything's good. The companion is good
[7:55] and uh codeex is connected
[7:57] automatically.
[7:58] All right. So, let's press continue. Uh,
[8:02] okay. Start searching.
[8:04] All right. So, it's working now to find
[8:08] us both uh selling and buying. That's a
[8:12] cool that. So, so it's going to start
[8:14] sourcing first. But if I wanted to add
[8:16] inventory, let's say I already have
[8:17] stuff, all I got to do is say iPhone um
[8:23] 11 128 gigabytes,
[8:27] good condition. I can just dictate this
[8:29] with a voice, however you want to put it
[8:31] in there. If you have an Excel file, you
[8:33] have lots of stuff already, you can
[8:35] already kind of like upload your CSV and
[8:37] just put everything in here all at once.
[8:40] Uh, and then you're going to click
[8:41] organize with AI, and it's going to
[8:44] basically add that for you. Um, preview
[8:47] rows, add one item.
[8:51] Okay. Now, uh, this one is going to need
[8:53] a photo right here. Um, and then, of
[8:56] course, you want to put in things like
[8:57] your price, uh, your asking price. You
[9:00] could put in your asking price if you
[9:02] want to put that firm, but it's going to
[9:03] already negotiate asking price for or or
[9:06] market price for you and it's going to
[9:07] try and find the best price to sell the
[9:10] thing. This is a good way uh adding the
[9:12] minimum is a good way to uh force it not
[9:16] to go too low.
[9:18] So, if you want to put that kind of
[9:20] safety barrier, that's what that minimum
[9:22] is there for. But it's going to research
[9:25] and find the product for you. So, uh,
[9:27] for the sake of this, I will skip that
[9:31] part. But, uh, once you add it, that's
[9:33] it. It starts right right away. Now, of
[9:35] course, you want to toggle the
[9:36] automations on and off. You just, uh,
[9:38] toggle on and off. Simple. And it's just
[9:41] going to get it to work. Okay. So, after
[9:44] a while, you'll start to see results in
[9:46] buying. So, this is your sourcing bot,
[9:48] and it's finding products for you. So,
[9:50] if you take a look in here, your buying
[9:53] tab, you're seeing results pop up. These
[9:56] are items it's currently negotiating.
[9:58] So, they've passed a certain threshold.
[9:59] They're still not out of the woods yet.
[10:01] It's still going to try and pass other
[10:03] thresholds. So, it has to find out how
[10:05] far it is from your location because you
[10:08] gave it a limit. So, it's going to
[10:09] calculate that and if it fails, it's
[10:11] going to kind of give you a notification
[10:13] here to either review or to pass on the
[10:15] item. You can also autopass if you set
[10:17] that up. It's going to just
[10:19] automatically disappear from here. And
[10:20] then from the selling tab, the items
[10:23] you've already have, you've already
[10:25] listed are going to have negotiations
[10:27] ongoing. It's doing that for you. So,
[10:30] the cool thing is, like I said, this is
[10:32] end to end automated. It's already
[10:35] figuring out the market prices for these
[10:37] things. Now, I think that's pretty much
[10:39] the whole system. Pretty easy to set up,
[10:41] pretty easy to get going. You guys are
[10:43] going to probably make it better, and
[10:44] that's the cool thing about just putting
[10:46] this out there. I'm really excited to
[10:48] see what you guys are going to do with
[10:49] this. So, if you guys find a way to make
[10:51] it better, you can uh push that fork or
[10:54] you can just let us know in the
[10:56] comments. We're all learning. AI is so
[10:58] new. There's so much we can build
[11:00] together with this stuff. And so, if you
[11:02] guys can make this better, if we can
[11:04] help anybody, you know, any contribution
[11:06] you can make can probably help quite a
[11:08] few people. Links in the comments. If
[11:10] you have any questions, let me know in
[11:11] the comments also and I'll see if
[11:12] [music] I can fix it. Thank you so much.
[11:14] Take care.

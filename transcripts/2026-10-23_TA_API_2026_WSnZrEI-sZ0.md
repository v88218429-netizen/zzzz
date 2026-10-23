# Google Trends API Python Tutorial - Search Volume And Trending Keywords

Source: https://www.youtube.com/watch?v=WSnZrEI-sZ0
Transcript method: FreeTranscriptAPI

## Transcript

[0:00] damn and there you have it I have
[0:01] relative search volume and trending
[0:03] keyword data from The Unofficial Google
[0:05] API in Python
[0:08] [Music]
[0:12] it's family it's your other brother Adam
[0:13] kit bags and today I'm going to be
[0:15] showing you how to get relative search
[0:16] volume and trending keyword data from
[0:20] the Google Trends in Python now this is
[0:22] important because you can do a lot of
[0:24] bag getting activities with this data so
[0:27] for example if you're doing SEO you can
[0:29] do your keyword research here for blog
[0:31] articles if you're doing e-commerce you
[0:33] can find trending products to add to
[0:35] your store or if you're doing degenerate
[0:37] gambling activities then you can find
[0:39] you know if let's say you want to see if
[0:41] there was a linear relationship between
[0:43] search volume and price then you could
[0:46] do some analysis there so let's go ahead
[0:48] and jump into a Google search pytrend
[0:51] docs then here the first one is going to
[0:53] be pipi.org let's click in there we're
[0:55] definitely going to need that the next
[0:57] Google search Google Trends and we can
[0:59] use this just to visualize the results
[1:01] that we get from our code are accurate
[1:03] to what's coming out of the web app so
[1:05] here we are on the python docs to scroll
[1:06] down to installation let's copy out this
[1:09] pip install py Trends and if you're
[1:10] using Anaconda then you can come over
[1:12] here go to environments go to base and
[1:14] then open up a terminal here pop that
[1:16] command in go ahead and run that once
[1:18] that gets installed and you can go back
[1:19] here and open up your spider so we flip
[1:21] back over to the docs we can copy this
[1:23] the next block out here which is going
[1:25] to connect us to Google so it's got to
[1:26] import then it's got our connection
[1:28] request here so here we are on the
[1:30] script all of my scripts are available
[1:31] on GitHub I'll post that link in the
[1:33] description so you can follow along with
[1:35] the code you see in the video here I've
[1:37] just pasted in this block from the docs
[1:39] here and then I've got a couple other
[1:40] Imports here just in case so let's go
[1:42] ahead and run these Imports and just
[1:44] make sure everything works all right it
[1:45] looks good no errors so back to the docs
[1:47] here it looks like they give you some
[1:48] information on connecting a proxy and
[1:51] then setting your time out here and you
[1:52] could read about it right here but let's
[1:54] go ahead and go down here to build our
[1:55] first payload so you can copy this and
[1:57] paste this into your script we'll use
[1:59] this as a first example so here I've
[2:01] changed our keyword to recession and
[2:03] then I've also created another keyword
[2:04] list here that has five different items
[2:07] now five is the max that you can use but
[2:09] we'll just save this for later here's
[2:10] our first payload it has the keyword is
[2:12] there with one item has all categories
[2:14] and then we have time frame so let's
[2:16] take a look at time frames in the
[2:17] documentation so if you scroll down here
[2:19] under common API parameters we have time
[2:23] frame information so we're just using
[2:25] this default time frame information for
[2:27] our first example but you could also
[2:29] look at all time frames then you can use
[2:31] specific dates we'll get into that you
[2:33] can even have a more granular look by
[2:34] using hourly data here then they also
[2:37] have current time minus time pattern
[2:38] which is just from today and then you
[2:40] specify the look back period so also
[2:42] interesting you can set your
[2:43] geographical region and then here we
[2:45] have different filters so you can look
[2:46] at images or YouTube as well as Google
[2:49] search so now that we understand these
[2:50] parameters let's go ahead and take a
[2:51] look at interest over time so you can
[2:54] copy this out what I've done is I've
[2:55] assigned this to a variable here after
[2:57] you build the payload then you can go
[2:59] ahead and run these functions so all I
[3:01] did was I set this variable here and
[3:03] then I've plotted the data but if we
[3:04] take a look at the variable we can see
[3:06] that we get a data frame back that has
[3:07] daily data here and then we have our
[3:09] keyword up top as a column and then we
[3:11] have the relative Trend data right here
[3:13] so I've plotted the relative volume data
[3:15] here let's go ahead and compare that
[3:17] against Google Trends in the web app so
[3:19] as you can see the graphs are very
[3:22] similar here so it looks like
[3:23] everything's working now up next what
[3:25] we're going to do is just take a look at
[3:26] a five item keyword list so I've ran
[3:28] that there to refresh our keyword list
[3:30] then we're going to rebuild our payload
[3:32] and then we're going to do the same
[3:33] thing there we're just going to take a
[3:34] look at the data frame looks we have all
[3:36] of our daily data then we have in our
[3:38] columns all of our keywords and then we
[3:40] have our relative search volume
[3:41] information right here so this is what a
[3:43] five-year chart of the data looks like
[3:45] all right that was fun but now let's
[3:47] take a look instead of the last five
[3:49] years of data we'll take a look at the
[3:52] last 12 months and you can see the
[3:54] parameter that we're using for time
[3:55] frame is this current time minus time
[3:57] pattern it's just today minus 12 months
[4:00] so you can see here's the last 12 months
[4:02] of data so here's the chart of our 12
[4:04] month data you can see these blockchain
[4:06] keywords are having some correlation
[4:08] here so up next we're going to take a
[4:10] look at the specific dates frame here so
[4:13] we can cherry pick a specific date range
[4:16] so as you can see there we're just
[4:17] looking at about 45 days from the end of
[4:20] 2016 to the beginning of 2017. so you
[4:23] can see the data there and then here is
[4:25] our chart so if you look back in the
[4:27] docs we have multi-range interest over
[4:28] time and historical hourly interest
[4:30] unfortunately couldn't get those to work
[4:33] but luckily we have a workaround for
[4:35] those let's look at minute data over the
[4:37] last four hours and we're going to use
[4:39] the now minus four hours as our time
[4:42] frame here and as we can see from our
[4:44] data frame here we do have minute data
[4:46] and then we have all of our columns as
[4:48] our keywords here and as we can see here
[4:50] our chart is much more granular awesome
[4:52] so up next we're going to look at our
[4:53] hour data for the last seven days we're
[4:56] going to use now and then minus the
[4:58] seven days as our time frame and as you
[5:00] can see from our time stamps there it is
[5:02] hourly data and then we have our data
[5:04] again here and here is our chart awesome
[5:06] up next we're going to look at more
[5:08] hourly data but we're going to specify a
[5:10] time frame so as you can see here the
[5:12] date parameters are a bit different they
[5:14] include an hour component there so you
[5:16] need to have t for time and then you
[5:18] have an hour so what we're going to do
[5:20] here is we're gonna get the previous
[5:22] seven days worth of data but starting
[5:24] seven days ago so as you can see that
[5:26] was a great success and we have our
[5:28] chart here as well now you might be
[5:30] saying to yourself if we got the last
[5:31] seven days of data and then we got seven
[5:33] days worth of data before that it's all
[5:36] in our granularity could you make some
[5:38] type of algorithm that just goes back
[5:40] and gets a larger data set of more
[5:43] granular search volume data so if you're
[5:45] going to concatenate all this data you
[5:47] have to be very careful and familiar
[5:48] with the data because this is relative
[5:50] search volume data so you have to
[5:52] understand if you change your time frame
[5:55] does that change the relative basis so
[5:58] does that make sense I don't mean to go
[6:00] down like a disclaimer Rabbit Hole or
[6:01] whatever but you need to be very careful
[6:03] about if you're going to concatenate
[6:05] data like this and make a business
[6:07] decisions awesome now that we got all
[6:09] that disclaimer BS out the way let's
[6:11] keep sending it we got interest by
[6:14] region here and you can specify a
[6:16] resolution so here we're going to look
[6:17] at Country level but they do have a
[6:19] couple other parameters and you can also
[6:21] include low volume regions so if you
[6:23] look at the data you can see our
[6:24] geographical location here and then you
[6:26] can also see all of your keywords and
[6:28] the respective values now next we're
[6:30] going to move on to related topics but
[6:32] what we need to do is instead of looking
[6:34] at a five item keyword list we need to
[6:37] look at a single item keyword list so
[6:40] I'm going to go ahead and just comment
[6:41] this out and then uncomment this so now
[6:43] we can just look at a single keyword and
[6:45] then we're going to go ahead and run our
[6:48] pytrends.related topics all right we
[6:51] look at the data I'm gonna rebuild the
[6:53] payload here and we're gonna just use
[6:55] the previous 12 months and now we are
[6:57] good to run our related topics query all
[7:00] right as you can see here it's a
[7:02] dictionary so let's look at the keys so
[7:04] our key is the first keyword on the list
[7:08] so we'll use that as our key and then
[7:10] it'll return us another dictionary so
[7:12] you can look at the keys and when you
[7:14] look at the keys we have rising and top
[7:16] as other keys so let's go ahead and take
[7:18] a look at Rising related topics all
[7:21] right here is a data frame so let's take
[7:23] a look at some of the columns we can
[7:25] look through and then let's go ahead and
[7:26] look through topic title that sounds
[7:28] interesting so here we have related
[7:30] topics to our keyword and you can see
[7:33] they're all kind of financial sounding
[7:34] so up next we can look at the value for
[7:36] our top Keys it looks like another data
[7:38] frame here awesome so similar columns
[7:40] and then we can take a look at topic
[7:41] titles and then we can see our topic
[7:43] titles here which are all very financy
[7:45] next in the docs we have related queries
[7:48] here so let's go ahead and let's run all
[7:51] right we can see it's another dictionary
[7:52] so let's take a look at the keys looks
[7:55] like it's our keyword again so we'll
[7:56] just look at the first item on our
[7:58] keyword list as our key and then we have
[8:01] another set of dictionaries so let's
[8:02] look at the keys again it's top and
[8:04] Rising so we'll just take a look at here
[8:06] as a data frame has query and then so
[8:09] here is a related query there and then
[8:11] it has a value associated with it and it
[8:13] just looks like everybody wants to know
[8:15] what that recession do next we can take
[8:17] a look at top and it looks like again
[8:19] top queries that are related you look at
[8:22] the columns it's the same and so
[8:24] obviously we can just add query and we
[8:26] can take a look at our top queries that
[8:28] are related up next in the docs we have
[8:30] trending searches and then you can
[8:32] search by geographical region so let's
[8:34] take a look at those all right very
[8:37] interesting we have our us trending
[8:39] searches I'm gonna let you in on a
[8:41] little secret I guarantee this is how
[8:42] some of those clickbaity YouTubers get
[8:44] their title tags Tomball some Jimmy
[8:46] Carter and Christian atsu had clinical
[8:49] depression after the Bad Bunny and Chris
[8:51] Brown concert Alright and then if we
[8:53] look at Japan trending just what I
[8:55] expected the Soul Brothers awesome next
[8:57] so we have our real-time search Trends
[9:00] and again you can specify by
[9:01] geographical region there so we can see
[9:04] the data here it looks like you gotta
[9:05] dig through this but you know I'll just
[9:07] leave that up to you up next we have our
[9:09] top charts and you can specify a
[9:11] geographical location here and then you
[9:13] also need to input a year so let's go
[9:16] ahead and see what's popping in 2022
[9:19] well I got bad news we have Wordle at
[9:22] the top of the list if that doesn't tell
[9:24] you everything you need to know next we
[9:27] have suggestions and you need to put a
[9:29] keyword in there so we're going to just
[9:30] go ahead and use the first item in our
[9:32] keyword list which is recession so I
[9:34] went ahead and ran that looks like we
[9:36] have a bunch of data that's not useful
[9:38] so I'd be very careful about how you use
[9:40] that all right and last but not least we
[9:42] got categories here and it looks like a
[9:43] dictionary so let's take a look at the
[9:45] keys looks we have children name and ID
[9:47] let's take a look at name it looks like
[9:48] we have all categories so let's find the
[9:50] show children of all categories all
[9:52] right it's a list so let's take a look
[9:54] at the first item in the list looks like
[9:56] another dictionary so let's take a look
[9:57] at the keys again it's the same format
[10:00] so let's take a look at the name it
[10:01] looks like arts and entertainment so
[10:03] let's see who are the children of arts
[10:05] and entertainment another list what a
[10:07] surprise and then we could take a look
[10:09] at the first item of that list it's
[10:10] gonna be celebrity news and
[10:12] entertainment and then we have the ID so
[10:13] if you want to specify category IDs so
[10:16] if you want to specify category IDs then
[10:18] now you know how to parse it out just
[10:20] add ID to the end of that bam and there
[10:23] you have it I have relative search
[10:24] volume and trending keyword data from
[10:26] The Unofficial Google API in Python
[10:29] please don't use what I showed you for
[10:31] evil only do good with it and don't do
[10:34] degenerate gambling activities anyways
[10:37] fam if you like the content subscribe to
[10:39] the channel come join the finance family
[10:40] we're growing appreciate everybody who's
[10:43] supporting you can always buy me a copy
[10:44] as always dodgeless red candles and
[10:47] let's go get these bags

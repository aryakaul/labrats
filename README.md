<h1 align="center"> labrats </h1>
<p align="center">
    <a href="#readme">
        <img alt="labrats" src="https://raw.githubusercontent.com/aryakaul/labrats/refs/heads/main/assets/ratteam.png">
    </a>
</p>
<p align="center"> 🐀🧑🏾‍🔬 - digest preprints w/ a team of personalized, custom, and adorable labrats </p>

---
## Problem
I recently defended my Ph.D. and one frustration I had throughout my time was an inability to sufficiently keep up with the firehose of scientific advances constantly occurring. Every month roughly 4,000 preprints get posted to bioRxiv. That means every week there are about 1,000 novel scientific preprints. I should note that these are *preprints* meaning (at least theoretically) they represent enough novel scientific work to justify a soon-to-be-published result. These aren't half-baked 20 minute WIP lunch seminars. Even if we operate under a conservative estimate that only 1% of those papers are relevant to your topic of interest (I find this quite silly as all scientists are seeking to better understand the Universe and there is only ONE Universe. Even if it doesn't seem like melanoma has no immediate connection to plant physiology, I reject the notion that there is absolutely *nothing* to be gained by at least superficially engaging with the plant field) then that means that there are 10 relevant papers being produced every week that demand careful, precise, and complete reading. Speaking for myself, I know that it was actively impossible to simultaneously juggle my own research and keeping abreast of the latest science. I first tried email digests, then RSS feeds, but both proved unwieldy. In the end, I settled for reading papers with interesting  twitter threads, those recommended by my friends and collaborators, and those that I stumbled upon while studying the background for my own research projects.

None of these approaches felt particularly satisfying, and I resigned myself to only getting the freedom to engage with the scientific literature when I finished my Ph.D. You can imagine my PLEASANT surprise to then see the rise of multiple, capable language models able to respond intelligently and ingest scientific material. I then saw numerous companies and public benefit corporations make a big hullaballoo around AI scientists and co-scientists. Setting aside how much of those claims are overblown versus real, it was frustrating to not find anybody doing what I thought would be one of the simplest and most useful applications of this technology in science -- triaging and streamlining the firehose of scientific research.

I then finished my Ph.D. and I now stand before you, wholly **_unshackled_** from the horrors of thesis writing. With this newfound freetime and freedom, I can now quixotically pursue each of my insane side projects. Thus, `labrats` was born.

Simply put, `labrats` is designed to simulate a Journal Club you might have with your labmates around a preprint. You provide a topic or keywords of interest, we pull all new preprints matching those preprints, and then a collection of LLMs (your team of labrats) ingest the abstracts and evaluate them. Each of these labrats inhabits a distinct persona meant to focus on distinct aspects of the work. I've coded some, but you can easily modify them however you want. 

After each labrat has had the chance to read all matching preprints, they each independently judge each preprint on a variety of metrics. These metrics, and the paper analysis, are then made accessible to the user. The goal is to build a team of labrats that can bubble up interesting papers you might have missed and ensure you are getting access to the papers most interesting to you. Everything is open-sourced and designed to be as extensible and modular as possible.

## Quick Start



<h1 align="center">labrats</h1>

<p align="center">
  <img alt="labrats" src="https://raw.githubusercontent.com/aryakaul/labrats/refs/heads/main/assets/labrats.png" width="640">
</p>

<p align="center">
  🐀🧑🏾‍🔬 &nbsp; triage preprints with a team of personalized labrats
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue.svg"></a>
  ![](./.github/vibecode.svg)
</p>

<p align="center">
  <a href="#what-is-labrats">About</a> •
  <a href="#meet-the-labrats">The Labrats</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#how-it-works">How It Works</a> •
  <a href="#configuration">Configuration</a>
</p>

<p align="center">
  <em>👋 New to the command line? Start with the <a href="https://github.com/aryakaul/labrats/wiki/Getting-Started">step-by-step Getting Started guide</a> — it walks you through everything, no prior experience required.</em>
</p>

---

## What is labrats?

> The number of new preprints is overwhelming. Managing which works to dive into is exceedingly difficult. 

`labrats` is a small, local tool that helps you triage preprints that might be worth diving into.

You provide a list of filtering criteria - keywords, categories, your own research context. `labrats` then pulls all the new preprints and a team of LLM-powered "labrats" (each playing a distinct persona) reads each abstract, scores it, and writes their thoughts. You can peruse the results in a clean webpage, and then pick those papers that you want to spend time reading in-depth. 

<p align="center">
  <img alt="labrats screenshot" src="https://raw.githubusercontent.com/aryakaul/labrats/refs/heads/main/assets/screenshot.png" width="780">
  <br>
  <sub><em>The digest view. Each card shows the team's headline score, a disagreement flag, and one button per labrat.</em></sub>
</p>

The code is open-source, it runs on your machine, and works with whatever LLM you want — OpenAI, Anthropic, Gemini, or locally installed models.

> 📝 My [substack post](TODO: link) has more details if you're interested.

---

## Meet your labrats

Each labrat reads the same abstract but cares about and prioritizes different things. Here are the defaults bundled with `labrats`:

| | Labrat | What they care about |
|---|---|---|
| 🎓 | **Excited Grad Student** | Novelty, curiosity, cross-field connections |
| 💼 | **Hype-Chasing PI** | Fundability, citations, who's behind it |
| 🔬 | **Postdoc Savant** | Technical depth, methodological elegance |
| 🦅 | **Reviewer 2** | Missing controls, alternative explanations, every flaw |
| 🧐 | **Skeptical Senior Scientist** | Released code, data, weights, statistical rigor |

You can edit any of these, disable them, or write your own — each labrat is just a YAML file. These are just the defaults I've encoded after spending time in the sciences.

---

## Quick start

### 👋 If you don't know what the command line is

The [**Getting Started guide**](https://github.com/aryakaul/labrats/wiki/Getting-Started) on the wiki walks you through everything — installing Python, installing `labrats`, and setting up a free local LLM so you don't have to worry about API keys. 

### If you're comfortable with the command line

```bash
# install
pipx install labrats           # or: uv tool install labrats

# first-time setup (creates ~/.config/labrats with example defaults)
labrats init

# add an API key in ~/.config/labrats/settings.yaml
# (or set OPENAI_API_KEY / ANTHROPIC_API_KEY / etc. in your shell)
# — or skip API keys entirely with a local model: see the wiki

# launch the web UI
labrats serve
```

The UI opens in your browser. You can go to the Settings tab to set up a personal profiles (on your topics of interest), click **Run**, and your labrats will get to work. 

> If you prefer the command line: `labrats run` does the same thing headless. I use this for automated runs via CRON jobs.

> See [Set up a free local LLM](https://github.com/aryakaul/labrats/wiki/Local-LLM-Setup) on the wiki.

---

## How it works

1. **Scrape** — fetch all new preprints from bioRxiv and arXiv matching the filters specified in your profile.
2. **Read** — each labrat then scores each abstract on rigor, novelty, and relevance. They then give a short written take.
3. **Synthesize** — averages scores into a headline number; categories with significant disagreements get flagged.
4. **Triage** — you can then skim the abstracts, see each labrat's perspective, and flag those papers that you find most interesting. 

Nothing leaves your machine except the API calls to whichever LLM provider you've configured.

---

## Configuration

Everything lives in `~/.config/labrats/`:

| File | What's in it |
|---|---|
| `settings.yaml` | API keys, default model, auto-run interval |
| `profiles.yaml` | Your topics: keywords, categories, which labrats to use |
| `personas/*.yaml` | One file per labrat — edit freely |

The web UI has a Settings tab that edits all of this for you, but the files are plain YAML if you'd rather hand-edit.

<details>
<summary><strong>API keys & security</strong></summary>

API keys are stored in `~/.config/labrats/settings.yaml`, which is automatically chmod'd to `0600` (readable only by your user). This matches what tools like `aws`, `gh`, and `kubectl` do.

If you'd rather not store keys on disk at all, set them as environment variables instead — `labrats` falls back to `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, etc.

**Don't commit `settings.yaml` to a repo.**
</details>

<details>
<summary><strong>Adding your own labrat</strong></summary>

Drop a YAML file into `~/.config/labrats/personas/`:

```yaml
name: The Translator
role: >
  A working clinician who reads every paper asking
  "could this change medical practice in the next five years?"
  You are unimpressed by elegant methods that don't
  connect to a real patient or decision.
scored_fields:
  - methodological_rigor
  - novelty
  - relevance
```

Restart the UI by quitting and rerunning `labrats serve` and your new labrat joins the team.

**Optional — give your labrat a face.** Drop a PNG next to the YAML
named after the file, e.g. `~/.config/labrats/personas/the_translator.png`.
It'll show up in the persona panel automatically. (You can also
override the bundled images this way.)
</details>

<details>
<summary><strong>Auto-run on open</strong></summary>

In the **Models** tab, set "Auto-run after N hours." When you open labrats, if the last run finished longer ago than that threshold, a fresh run kicks off automatically. Set to `0` to disable.

For true background runs while your machine is closed, schedule `labrats run` via cron / launchd / Task Scheduler.
</details>

---

## Status

Early days. Bugs exist. Feedback and PRs welcome — open an [issue](https://github.com/aryakaul/labrats/issues) or hop in to chat.

## License

See [LICENSE](LICENSE).

<p align="center">
  <sub>made by <a href="https://arya.casa">Arya Kaul</a> · 🐀</sub>
</p>

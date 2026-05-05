<h1 align="center">labrats</h1>

<p align="center">
  <img alt="labrats" src="https://raw.githubusercontent.com/aryakaul/labrats/refs/heads/main/assets/labrats.png" width="640">
</p>

<p align="center">
  <em>🐀🧑🏾‍🔬 &nbsp; triage preprints with a team of personalized labrats</em>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue.svg"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-see%20LICENSE-green.svg"></a>
  <a href="https://github.com/aryakaul/labrats/issues"><img alt="Issues" src="https://img.shields.io/github/issues/aryakaul/labrats.svg"></a>
  <img alt="Status: early" src="https://img.shields.io/badge/status-early-orange.svg">
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

> There are too many preprints. Nobody can read them all, and the ones worth reading are buried under the ones that aren't.

**labrats** is a small, local tool that helps you find the preprints actually worth your time.

You tell it what you care about — keywords, categories, your own research context. It pulls fresh preprints from **bioRxiv** and **arXiv**. Then a team of LLM "labrats" — each playing a distinct persona — reads every one, scores it, and writes a short summary. You see the results in a clean web UI, sorted by what your team found most interesting.

<p align="center">
  <img alt="labrats screenshot" src="https://raw.githubusercontent.com/aryakaul/labrats/refs/heads/main/assets/screenshot.png" width="780">
  <br>
  <sub><em>The digest view. Each card shows the team's headline score, a disagreement flag, and one button per labrat.</em></sub>
</p>

It's open-source, runs on your machine, and works with whatever LLM you want — OpenAI, Anthropic, Gemini, Groq, or a local Ollama model.

> 📝 Want the longer story? Read the [Substack post](TODO: link) for the why.

---

## Meet the labrats

Each labrat reads the same paper but cares about different things. **Disagreement is itself a signal** — the UI flags papers where the team didn't see eye to eye.

| | Labrat | What they care about |
|---|---|---|
| 🎓 | **Excited Grad Student** | Novelty, curiosity, cross-field connections |
| 💼 | **Hype-Chasing PI** | Fundability, citations, who's behind it |
| 🔬 | **Postdoc Savant** | Technical depth, methodological elegance |
| 🦅 | **Reviewer 2** | Missing controls, alternative explanations, every flaw |
| 🧐 | **Skeptical Senior Scientist** | Released code, data, weights, statistical rigor |

You can edit any of these, disable them, or write your own — labrats are just YAML files.

---

## Quick start

### 👋 New to this?

The [**Getting Started guide**](https://github.com/aryakaul/labrats/wiki/Getting-Started) on the wiki walks you through everything — installing Python, installing labrats, and setting up a **free local LLM** so you don't need an API key or a credit card. Step-by-step, with screenshots, on macOS, Windows, or Linux.

### Already comfortable with the command line?

```bash
# install
pipx install labrats           # or: uv tool install labrats

# first-time setup (creates ~/.config/labrats with sensible defaults)
labrats init

# add an API key in ~/.config/labrats/settings.yaml
# (or set OPENAI_API_KEY / ANTHROPIC_API_KEY / etc. in your shell)
# — or skip API keys entirely with a local model: see the wiki

# launch the web UI
labrats serve
```

The UI opens in your browser. Set up a profile (your topic of interest), click **Run**, and your labrats will get to work.

> Prefer the command line? `labrats run` does the same thing headless — handy for cron, systemd, or launchd.

> 🆓 Want to run labrats **without paying for an API**? See [Set up a free local LLM](https://github.com/aryakaul/labrats/wiki/Local-LLM-Setup) on the wiki.

---

## How it works

```
   ┌────────┐     ┌─────────┐     ┌──────────┐     ┌────────┐
   │ scrape │ ──▶ │  read   │ ──▶ │synthesize│ ──▶ │ triage │
   └────────┘     └─────────┘     └──────────┘     └────────┘
   bioRxiv +      each labrat     average scores,   you skim a
   arXiv          scores +         flag disputes    sorted feed
                  summarizes
```

1. **Scrape** — fetches new preprints from bioRxiv and arXiv matching your profile.
2. **Read** — each labrat scores each abstract on rigor, novelty, and relevance, plus a short written take.
3. **Synthesize** — scores average into a headline number; disagreements get flagged.
4. **Triage** — you skim cards, drill into a paper, see each labrat's perspective, and re-run any of them on demand.

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

If you'd rather not store keys on disk at all, set them as environment variables instead — labrats falls back to `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, etc.

**Don't commit `settings.yaml` to a repo.**
</details>

<details>
<summary><strong>Adding your own labrat</strong></summary>

Drop a YAML file into `~/.config/labrats/personas/`:

```yaml
name: The Translator
role: >
  A working clinician who reads every paper asking
  "could this change practice in the next five years?"
  You are unimpressed by elegant methods that don't
  connect to a real patient or decision.
scored_fields:
  - methodological_rigor
  - novelty
  - relevance
```

That's it. Restart the UI and your new labrat joins the team.
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

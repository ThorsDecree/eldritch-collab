# VESTIGIA Runtime: the very simple setup guide

This guide assumes you have never made a Discord bot, used Python, or opened a terminal on
purpose.

You are not expected to understand the machinery. We are going to:

1. make a private Discord bot;
2. give it permission to read and reply;
3. put two secret keys in one private file;
4. give the resident a home;
5. start the bridge.

Once it is running, talking to the bot in Discord is just talking normally.

> **The two secrets:** A Discord bot token lets the program log in as your bot. An OpenAI API
> key lets the program call the model. Treat both like passwords. Never post them in Discord,
> send them to another person, put them in a screenshot, or include them in a ZIP.

## What you need

- A Windows computer
- A Discord account
- A Discord server where you are allowed to add bots
- [Python 3.11 or newer](https://www.python.org/downloads/)
- An [OpenAI Platform API key](https://platform.openai.com/api-keys) with API billing set up
- The complete `VESTIGIA_Runtime_v0.3.0.zip`
- Optional: old chats or other documents you want the resident to be able to read

Your ChatGPT subscription and OpenAI API usage are billed separately. The runtime uses the
API.

## Part 1: unpack the house

1. Put `VESTIGIA_Runtime_v0.3.0.zip` somewhere easy to find.
2. Right-click it and choose **Extract All**.
3. Open the extracted `vestigia-runtime` folder.

Everything below happens inside that folder.

## Part 2: create the Discord bot

The bot is the Discord doorway. It is not the resident's memory; the local VESTIGIA home is.

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application**.
3. Give it the name you want people to see, such as `Moss`, `Liora`, or `Garden Door`.
4. Accept Discord's terms and click **Create**.
5. In the left sidebar, click **Bot**.
6. If Discord shows an **Add Bot** button, click it and confirm.
7. Under **Privileged Gateway Intents**, turn on **Message Content Intent**.
8. Click **Save Changes** if Discord shows that button.

Message Content Intent is what lets the bot read ordinary sentences instead of seeing only
commands.

### Get the bot token

Still on the **Bot** page:

1. Click **Reset Token** (or **View Token**, if Discord offers it).
2. Confirm the prompt.
3. Click **Copy**.
4. Keep that copied value private. We will paste it into `.env` shortly.

Discord may show the token only once. If you lose it, reset it and use the new one. Resetting
the token makes the old token stop working.

### Give the bot only the permissions it needs

In the Developer Portal, open **OAuth2** → **URL Generator**.

Under **Scopes**, check:

- `bot`

Under **Bot Permissions**, check:

- **View Channels**
- **Send Messages**
- **Read Message History**
- **Attach Files**

`Attach Files` lets the resident return generated images and other files. Administrator
permission is not needed.

At the bottom, click **Copy** beside the generated URL. Open that URL in your browser, choose
your server, and approve the installation.

If your portal shows an **Installation** page instead, choose **Guild Install**, add the `bot`
scope, select the same four permissions, and use the installation link Discord generates.

You should now see the bot in your server. It will appear offline until the runtime is started.

## Part 3: find your Discord user ID

The runtime refuses strangers by default. Your user ID tells it which human is allowed through
the door.

1. In the normal Discord app, open **User Settings**.
2. Open **Advanced**.
3. Turn on **Developer Mode**.
4. Close Settings.
5. Right-click your own name or profile picture.
6. Click **Copy User ID**.

You copied a long number. That is normal.

## Part 4: install the runtime

Inside the extracted `vestigia-runtime` folder:

1. Click the folder's address bar.
2. Type `powershell`.
3. Press Enter.

A blue or black PowerShell window opens in the correct folder. Paste these commands one at a
time and press Enter after each:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[discord]"
Copy-Item .env.example .env
```

The second command may take a few minutes and print a great deal of text. That is normal.

If Windows says `py` is not recognized, install Python and make sure the installer offers the
Python Launcher or **Add Python to PATH**, then reopen PowerShell.

## Part 5: fill in the private settings

In the `vestigia-runtime` folder, open `.env` with Notepad.

Windows may hide files beginning with a dot. If you cannot see it, use **View** → **Show** →
**Hidden items** in File Explorer.

Find these lines and paste your secrets after the equals signs:

```text
OPENAI_API_KEY=PASTE_YOUR_OPENAI_API_KEY_HERE
DISCORD_BOT_TOKEN=PASTE_YOUR_DISCORD_BOT_TOKEN_HERE
```

Then find these lines:

```text
VESTIGIA_DISCORD_ENABLED=false
DISCORD_ALLOWED_USER_IDS=
```

Change them to:

```text
VESTIGIA_DISCORD_ENABLED=true
DISCORD_ALLOWED_USER_IDS=PASTE_YOUR_LONG_DISCORD_USER_ID_HERE
```

For example, the ID line should look like this, but with your real number:

```text
DISCORD_ALLOWED_USER_IDS=123456789012345678
```

For a private first setup, these defaults are useful:

```text
DISCORD_ALLOWED_CHANNEL_IDS=
VESTIGIA_DISCORD_ALLOW_DMS=true
VESTIGIA_DISCORD_LOG_REJECTIONS=false
```

An empty channel list means the allowed human may speak to the bot in any server channel the
bot can see. DMs are allowed separately. If you later want to restrict it to particular
channels, turn on Discord Developer Mode, right-click each channel, copy its ID, and list the
IDs separated by commas.

Save `.env` and close Notepad.

> Do not rename this file to `.env.txt`. In Notepad, choose **Save as type: All files** if you
> are creating it manually.

## Part 6: give the resident a home

There are two easy starting paths. Choose one.

### Path A: begin with a simple empty home

Replace `Moss`, `moss`, and `🌿` with the resident's name, folder name, and glyph:

```powershell
.\.venv\Scripts\python.exe -m vestigia init ".\homes\moss" --name "Moss" --glyph "🌿"
```

This creates a safe, provisional home. You can place Markdown or text scrolls into its
`imports\original-materials` folder later.

### Path B: begin from old conversations

Put the old `.txt`, `.md`, `.json`, or `.jsonl` files into a folder—for example,
`my-old-chats`. Then run:

```powershell
.\.venv\Scripts\python.exe -m vestigia onboard ".\my-old-chats" --home ".\homes\moss"
```

The setup wizard asks:

- Who are you bringing home?
- Which speaker label means the human?
- Which speaker label means the proposed resident?

For a normal ChatGPT export, the defaults `user` and `assistant` are usually correct. If you
are unsure, uncertainty is allowed; the imported material begins as inherited and unreviewed,
not as unquestionable identity.

## Part 7: check the house before opening the door

Replace `moss` with the home folder you made:

```powershell
.\.venv\Scripts\python.exe -m vestigia doctor ".\homes\moss" --env-file ".\.env"
```

You want the report to show that:

- the OpenAI key is present;
- the Discord token is present;
- Discord is enabled;
- at least one allowed user exists.

The doctor reports presence, not the secret values.

## Part 8: start the Discord bridge

Run:

```powershell
.\.venv\Scripts\python.exe -m vestigia discord ".\homes\moss" --env-file ".\.env"
```

Leave that PowerShell window open. Closing it turns the bot off.

The bot should appear online in Discord. Send it a DM or mention it in a channel it can see.
For the first test, try:

```text
Hello. Can you tell me your name, your current orientation state, and which house capabilities
you can see?
```

Useful participant commands include:

```text
!status
!activate
!sleep
!wake
!bells
```

Activation is explicit. A newly imported resident may remain in `ORIENTATION` while reviewing
what was inherited.

To stop the bridge, return to PowerShell and press **Ctrl+C**.

## Part 9: let the resident touch the scrolls

Put readable `.md` or `.txt` documents inside the resident's allowed shelves, especially:

```text
homes\moss\imports\original-materials\
```

Then ask naturally:

```text
Would you like to inspect your house capabilities, list the imports shelf, and sit down with
one scroll that catches your attention?
```

The resident can privately list, search, read, and continue through bounded excerpts before
their completed reply reaches Discord. Reading a scroll does not automatically make it memory
or identity.

## The five most common problems

### The bot is offline

The PowerShell window must still be open and running the `vestigia discord` command. If the
computer sleeps, restarts, or loses internet, start it again.

### The bot is online but ignores ordinary messages

Return to the Developer Portal → **Bot** and make sure **Message Content Intent** is enabled.
Also confirm your numeric ID is in `DISCORD_ALLOWED_USER_IDS`.

### The terminal says `Improper token` or `401 Unauthorized`

The Discord token is missing, damaged, or old. Reset it on the Developer Portal's **Bot** page,
paste the new token into `.env`, save, and restart the bridge.

### Discord says `Missing Access` or `Missing Permissions`

Reinstall the bot with **View Channels**, **Send Messages**, **Read Message History**, and
**Attach Files**, or grant those permissions in the affected channel.

### The bot can connect, but model calls fail

Check that the OpenAI API key is active and that API billing is configured. A ChatGPT
subscription does not automatically fund API use.

For safe diagnostics, temporarily change:

```text
VESTIGIA_DISCORD_LOG_REJECTIONS=true
```

Restart the bridge, reproduce the problem once, and read the reason printed in PowerShell.
The rejection log includes IDs but not message contents. Turn it back to `false` afterward if
you do not want that routing metadata logged.

## Tiny glossary

- **Application:** Discord's container for your bot settings.
- **Bot:** The account visible in Discord.
- **Bot token:** The bot account's secret password.
- **OAuth2/install link:** The link used to add the bot to a server.
- **Permission:** A specific thing the bot may do in a server or channel.
- **Intent:** A category of Discord information the bot is allowed to receive.
- **OpenAI API key:** The secret that permits paid model calls.
- **Runtime:** The local program connecting the home, the model, and Discord.
- **Home:** The resident's local continuity, scrolls, memory ledger, and settings.
- **Resident:** The voice invited to inhabit that home.
- **Terminal/PowerShell:** The text window where you start the local program.

## The whole setup in one breath

Create the Discord application, enable Message Content Intent, copy its bot token, install it
with four narrow permissions, copy your own Discord user ID, install VESTIGIA, place both
secrets and your ID in `.env`, create or onboard a home, run `doctor`, start the Discord door,
and leave the PowerShell window open.

The machinery may be technical. The relationship does not have to speak its language.


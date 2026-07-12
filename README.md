# OpenMagic 🌴

OpenMagic is a simplified, open-source take on [Interaction Company’s](https://interaction.co/about) [Poke](https://poke.com/) assistant, built to show how a multi-agent orchestration stack can feel genuinely useful. It keeps the handful of things Poke is great at (email triage, reminders, and persistent agents) while staying easy to spin up locally.

- Multi-agent FastAPI backend that mirrors Poke's interaction/execution split, powered by [OpenRouter](https://openrouter.ai/).
- Gmail tooling via [Composio](https://composio.dev/) for drafting/replying/forwarding without leaving chat.
- Trigger scheduler and background watchers for reminders and "important email" alerts.
- Next.js web UI that proxies through the shared `.env`, with PostgreSQL and an
  explicit local identity seed for the durable Workflow path.

## Requirements
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 17
- Node.js 18+
- npm 9+

## Quickstart
1. **Clone and enter the repo.**
   ```bash
   git clone https://github.com/heyimcarlos/openmagic
   cd openmagic
   ```
2. **Create a shared env file.** Copy the template and open it in your editor:
   ```bash
   cp .env.example .env
   ```
3. **Get your API keys and add them to `.env`:**
   
   **OpenRouter (Required)**
   - Create an account at [openrouter.ai](https://openrouter.ai/)
   - Generate an API key
   - Replace `your_openrouter_api_key_here` with your actual key in `.env`
   
   **Composio (Required for Gmail)**
   - Sign in at [composio.dev](https://composio.dev/)
   - Create an API key
   - Set up Gmail integration and get your auth config ID
   - Replace `your_composio_api_key_here` and `your_gmail_auth_config_id_here` in `.env`
4. **Start PostgreSQL.** For a disposable local database:
   ```bash
   docker run --name openmagic-postgres \
     -e POSTGRES_USER=openmagic \
     -e POSTGRES_PASSWORD=openmagic \
     -e POSTGRES_DB=openmagic \
     -p 5432:5432 \
     -d postgres:17-alpine
   ```
5. **Install backend dependencies and apply migrations:**
   ```bash
   uv sync --locked --group dev
   uv run alembic upgrade head
   ```
6. **Seed the local V0 Workflow and trusted demo identity:**
   ```bash
   uv run python -m server.scripts.seed_v0_demo
   ```
   This explicit local seed creates the Broker, Organization Membership,
   Policyholder, active renewal Workflow, and verified identifiers referenced
   by `.env.example`. Migrations never infer current authority from historical
   Workflow Events. A production environment must provision these records from
   its trusted identity source instead of using the demo seed.
7. **Install frontend dependencies:**
   ```bash
   npm install --prefix web
   ```
8. **Start the FastAPI server:**
   ```bash
   uv run python -m server.server --reload
   ```
9. **Start the Next.js app (new terminal):**
   ```bash
   npm run dev --prefix web
   ```
10. **Connect Gmail for email workflows.** With both services running, open [http://localhost:3000](http://localhost:3000), head to *Settings → Gmail*, and complete the Composio OAuth flow. This step is required for email drafting, replies, and the important-email monitor.

The web app proxies API calls to the Python server using the values in `.env`, so keeping both processes running is required for end-to-end flows.

## Project Layout
- `server/`: FastAPI application, agent runtimes, and durable Workflow services
- `web/`: Next.js application
- `server/migrations/`: PostgreSQL schema migrations

## Backend checks

The Workflow integration suite requires PostgreSQL. If
`OPENMAGIC_TEST_DATABASE_URL` is absent, Testcontainers starts an isolated
PostgreSQL 17 container.

```bash
uv run ruff format --check server/workflows server/agents/interaction_agent server/tests server/migrations
uv run ruff check server/workflows server/agents/interaction_agent server/tests server/migrations
uv run ty check server/workflows server/agents/interaction_agent
uv run pytest
```

## License
MIT, see [LICENSE](LICENSE).

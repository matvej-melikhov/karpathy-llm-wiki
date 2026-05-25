import type { Plugin } from "@opencode-ai/plugin"

const fireAndForget = (p: any) => p.nothrow().quiet().then(() => {}).catch(() => {})

export const WikiHooks: Plugin = async ({ $, directory, client }) => ({
  event: async ({ event }) => {
    if (event.type === "session.idle") {
      try {
        const out = await $`cd ${directory} && [ -d wiki ] && [ -d .git ] && git diff --name-only HEAD 2>/dev/null | grep -q '^wiki/' && echo changed || true`
          .nothrow()
          .quiet()
          .text()
        if (out.trim() === "changed") {
          await client.app.log({
            body: {
              service: "wiki-hooks",
              level: "info",
              message:
                "wiki/ изменена в этой сессии — обнови wiki/cache.md (до 500 слов: Последнее обновление, Ключевые факты, Недавние изменения).",
            },
          })
        }
      } catch (_) {}

      fireAndForget(
        $`cd ${directory} && [ -f .env ] && set -a && . ./.env && set +a; [ -f bin/embed.py ] && python3 bin/embed.py update`,
      )
      fireAndForget($`cd ${directory} && [ -f bin/gen_dashboards.py ] && python3 bin/gen_dashboards.py`)
      fireAndForget($`cd ${directory} && [ -f bin/gen_index.py ] && python3 bin/gen_index.py`)
      fireAndForget($`afplay /System/Library/Sounds/Glass.aiff`)
    }

    if (event.type === "session.compacted") {
      try {
        await client.app.log({
          body: {
            service: "wiki-hooks",
            level: "info",
            message:
              "Контекст компактнулся — перечитай wiki/cache.md, чтобы восстановить недавний контекст.",
          },
        })
      } catch (_) {}
    }
  },
})

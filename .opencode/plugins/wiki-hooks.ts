import type { Plugin } from "@opencode-ai/plugin"

export const WikiHooks: Plugin = async ({ $, directory, client }) => {
  const log = (message: string, level: "info" | "warn" | "error" = "info") =>
    client.app.log({ body: { service: "wiki-hooks", level, message } }).catch(() => {})

  const dispatchPostTurn = () => {
    try {
      // @ts-ignore — Bun is opencode's runtime
      Bun.spawn(["sh", "-c", `nohup '${directory}/bin/post-turn.sh' </dev/null >/dev/null 2>&1 &`], {
        cwd: directory,
        stdio: ["ignore", "ignore", "ignore"],
      })
    } catch (_) {}
  }

  return {
    event: async ({ event }) => {
      if (event.type === "session.idle") {
        dispatchPostTurn()
        try {
          const out = await $`cd ${directory} && [ -d wiki ] && [ -d .git ] && git diff --name-only HEAD 2>/dev/null | grep -q '^wiki/' && echo changed || true`
            .nothrow()
            .quiet()
            .text()
          if (out.trim() === "changed") {
            await log(
              "wiki/ изменена в этой сессии — обнови wiki/cache.md (до 500 слов: Последнее обновление, Ключевые факты, Недавние изменения).",
            )
          }
        } catch (_) {}
      }

      if (event.type === "session.compacted") {
        await log(
          "Контекст компактнулся — перечитай wiki/cache.md, чтобы восстановить недавний контекст.",
        )
      }
    },
  }
}

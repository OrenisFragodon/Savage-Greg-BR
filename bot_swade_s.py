import os
import random
import re
import discord

# ---------- Config ----------
TOKEN = os.getenv("DISCORD_TOKEN", "COLOQUE_SEU_TOKEN_AQUI")

T_DEFAULT = 4          # Dificuldade padrão
WILD_DEFAULT = 6       # Selvagem padrão (d6)

INTENTS = discord.Intents.default()
INTENTS.message_content = True
client = discord.Client(intents=INTENTS)

# ------------------------------------------------------------
# Padrões de comandos
# ------------------------------------------------------------
# Testes com selvagem (grupo e individuais):
CMD_PATTERN_GROUP = re.compile(
    r"^\s*(\d*)\s*s(4|6|8|10|12)"
    r"(?:\s*([+-]\s*\d+))?"
    r"(?:\s*t\s*(\d+))?\s*$",
    re.IGNORECASE
)

CMD_PATTERN_INDIV = re.compile(
    r"^\s*(\d+)\s*#\s*s(4|6|8|10|12)"
    r"(?:\s*([+-]\s*\d+))?"
    r"(?:\s*t\s*(\d+))?\s*$",
    re.IGNORECASE
)

# Dano (SEM selvagem) — suporta até DOIS termos (XdY [+ WdZ]) e T<dif>
# Grupo:      XdY [+ WdZ] [±mod] [T<dif>]
DMG_PATTERN_GROUP = re.compile(
    r"^\s*(\d*)\s*d(4|6|8|10|12|20)"
    r"(?:\s*\+\s*(\d*)\s*d(4|6|8|10|12|20))?"
    r"(?:\s*([+-]\s*\d+))?"
    r"(?:\s*t\s*(\d+))?\s*$",
    re.IGNORECASE
)
# Individuais: M#XdY [+ WdZ] [±mod] [T<dif>]
DMG_PATTERN_INDIV = re.compile(
    r"^\s*(\d+)\s*#\s*(\d*)\s*d(4|6|8|10|12|20)"
    r"(?:\s*\+\s*(\d*)\s*d(4|6|8|10|12|20))?"
    r"(?:\s*([+-]\s*\d+))?"
    r"(?:\s*t\s*(\d+))?\s*$",
    re.IGNORECASE
)

# DS em qualquer posição (troca o dado selvagem SÓ nessa jogada):
DS_PATTERN = re.compile(r"\bds\s*(6|8|10|12)\b", re.IGNORECASE)

MAX_COUNT = 20

# ------------------------------------------------------------
# Utilidades de rolagem
# ------------------------------------------------------------
def roll_ace(die_size: int):
    total = 0
    rolls = []
    while True:
        r = random.randint(1, die_size)
        rolls.append(r)
        total += r
        if r == die_size:  # explode
            continue
        break
    return total, rolls

def fmt_rolls(rolls, die_size):
    def fmt(r):
        return f"**{r}**" if r == die_size else str(r)
    return "[" + ", ".join(fmt(r) for r in rolls) + "]"

def parse_mod(s):
    if not s:
        return 0
    s = s.replace(" ", "")
    return int(s)

def assess(final_val: int, diff_value: int):
    """('fail'|'success'|'raises', raises_int)"""
    if final_val < diff_value:
        return "fail", 0
    raises = (final_val - diff_value) // 4
    if raises >= 1:
        return "raises", raises
    return "success", 0

def score(final_val: int, diff_value: int) -> int:
    """0=falha; 1=sucesso; 1+N=sucesso com N ampliações."""
    status, raises = assess(final_val, diff_value)
    if status == "fail":
        return 0
    return 1 + raises

def color_for(final_val: int, diff_value: int):
    status, _ = assess(final_val, diff_value)
    if status == "fail":
        return 0xE24C4B  # red
    if status == "raises":
        return 0xF1C40F  # gold
    return 0x2ECC71      # green

# Emotes por teste para títulos (modo s…)
def title_emote_token(final_val: int, T_value: int, is_crit: bool = False) -> str:
    if is_crit:
        return "💀"
    status, raises = assess(final_val, T_value)
    if status == "fail":
        return "❌"
    return "✅" + (f" 🏅x{raises}" if raises > 0 else "")

# Emote para títulos de DANO (não existe falha crítica em dano)
def title_emote_damage(final_val: int, T_value: int) -> str:
    status, raises = assess(final_val, T_value)
    if status == "fail":
        return "❌"
    return "✅" + (f" 🏅x{raises}" if raises > 0 else "")

# ------------------------------------------------------------
# Lógica especial do grupo NsX: aplicar UM selvagem ao melhor slot
# ------------------------------------------------------------
def apply_wild_to_best_slot(trait_finals, wild_final, T_value, first_trait_vals, wild_first):
    """
    Decide em qual teste aplicar o Selvagem para maximizar o resultado:
    - Prioriza transformar falha em sucesso; depois, aumentar ampliação.
    - Se não melhora nada, não aplica.
    Retorna: (eff_finals, used_idx, crit_idx)
    """
    n = len(trait_finals)
    base_scores = [score(v, T_value) for v in trait_finals]
    alt_scores  = [score(max(v, wild_final), T_value) for v in trait_finals]
    improvements = [alt_scores[i] - base_scores[i] for i in range(n)]

    used_idx = None
    if max(improvements) > 0:
        best_imp = max(improvements)
        candidates = [i for i, imp in enumerate(improvements) if imp == best_imp]
        used_idx = min(candidates, key=lambda i: base_scores[i])  # arruma falha primeiro

    eff_finals = [max(v, wild_final) if i == used_idx else v for i, v in enumerate(trait_finals)]

    crit_idx = None
    if used_idx is not None and first_trait_vals[used_idx] == 1 and wild_first == 1:
        crit_idx = used_idx

    return eff_finals, used_idx, crit_idx

# ------------------------------------------------------------
# Embeds — Testes com Selvagem
# ------------------------------------------------------------
def build_group_embed(
    count: int, die: int, mod_all: int, T_value: int, wild_size: int,
    wild_total: int, wild_rolls, wild_final: int,
    trait_results: list
):
    # Finais crus dos traços e primeiros (p/ crítica)
    trait_finals = [t["final"] for t in trait_results]
    first_trait_vals = [t["rolls"][0] for t in trait_results]
    wild_first = wild_rolls[0]

    # Aplica Selvagem em UM slot (se melhorar)
    eff_finals, used_idx, crit_idx = apply_wild_to_best_slot(
        trait_finals, wild_final, T_value, first_trait_vals, wild_first
    )

    # Título: tokens por teste separados por |
    tokens = [title_emote_token(val, T_value, (i == crit_idx)) for i, val in enumerate(eff_finals)]
    title = " | ".join(tokens)
    if len(title) > 250:  # segurança
        title = title[:247] + "…"

    # Melhor efetivo para cor/resultado
    best_value = max(eff_finals) if eff_finals else wild_final

    # Descrição
    desc_parts = [f"Dif={T_value}"]
    if mod_all:
        desc_parts.append(f"mod {mod_all:+d}")
    if wild_size != WILD_DEFAULT:
        desc_parts.append(f"selv=d{wild_size}")
    description = " | ".join(desc_parts)

    embed = discord.Embed(title=title, description=description, color=color_for(best_value, T_value))

    # Selvagem
    embed.add_field(
        name=f"d{wild_size} (Selvagem)",
        value=f"{fmt_rolls(wild_rolls, wild_size)}\nTotal: **{wild_total}** ⇒ **{wild_final}**",
        inline=False
    )

    # Traços (sem a palavra "Traços")
    if count == 1:
        t = trait_results[0]
        crit = " • ⚠️ Falha Crítica" if (crit_idx == 0) else ""
        embed.add_field(
            name=f"d{die}",
            value=f"{fmt_rolls(t['rolls'], die)}\nTotal: **{t['total']}** ⇒ **{t['final']}**{crit}",
            inline=False
        )
    else:
        lines = [f"#{t['idx']}: {fmt_rolls(t['rolls'], die)} ⇒ **{t['final']}**" for t in trait_results]
        chunk = ""
        block_idx = 1
        for line in lines:
            if len(chunk) + len(line) + 1 > 1000:
                embed.add_field(name=f"d{die} (parte {block_idx})", value=chunk, inline=False)
                block_idx += 1
                chunk = ""
            chunk += (line + "\n")
        if chunk:
            embed.add_field(name=f"d{die}{' (parte ' + str(block_idx) + ')' if block_idx>1 else ''}", value=chunk, inline=False)

    # Resultado: Final & Ampliações
    _, raises = assess(best_value, T_value)
    embed.add_field(name="Resultado", value=f"Final: **{best_value}**  |  Ampliações: **{raises}**", inline=False)

    if used_idx is not None and count > 1:
        embed.set_footer(text=f"Selvagem aplicado ao teste #{used_idx + 1}")

    return embed

def build_individuals_embed(count: int, die: int, mod_all: int, T_value: int, wild_size: int, tests: list):
    # Título: tokens por teste (um por teste), separados por |
    tokens = [title_emote_token(t["best_value"], T_value, t["crit"]) for t in tests]
    title = " | ".join(tokens)
    if len(title) > 250:
        title = title[:247] + "…"

    ref = tests[0]["best_value"] if tests else 0
    desc = f"Dif={T_value}"
    if mod_all:
        desc += f" | mod {mod_all:+d}"
    if wild_size != WILD_DEFAULT:
        desc += f" | selv=d{wild_size}"

    embed = discord.Embed(title=title, description=desc, color=color_for(ref, T_value))

    if count > 10:
        lines = []
        for i, t in enumerate(tests, start=1):
            _, raises = assess(t["best_value"], T_value)
            crit = " ⚠️" if t["crit"] else ""
            lines.append(
                f"#{i}: d{die} {fmt_rolls(t['trait_rolls'], die)} ⇒ **{t['trait_final']}** | "
                f"Selv {fmt_rolls(t['wild_rolls'], wild_size)} ⇒ **{t['wild_final']}** | "
                f"Final: **{t['best_value']}**  |  Ampliações: **{raises}**{crit}"
            )
        chunk = ""
        block_idx = 1
        for line in lines:
            if len(chunk) + len(line) + 1 > 1000:
                embed.add_field(name=f"Testes (parte {block_idx})", value=chunk, inline=False)
                block_idx += 1
                chunk = ""
            chunk += (line + "\n")
        if chunk:
            embed.add_field(name=f"Testes{'' if block_idx==1 else f' (parte {block_idx})'}", value=chunk, inline=False)
    else:
        for i, t in enumerate(tests, start=1):
            _, raises = assess(t["best_value"], T_value)
            crit = " • ⚠️ Falha Crítica" if t["crit"] else ""
            embed.add_field(
                name=f"Teste #{i}",
                value=(
                    f"• Resultado: Final **{t['best_value']}**  |  Ampliações: **{raises}**{crit}\n"
                    f"• d{die}: {fmt_rolls(t['trait_rolls'], die)} ⇒ **{t['trait_final']}**\n"
                    f"• d{wild_size} (Selvagem): {fmt_rolls(t['wild_rolls'], wild_size)} ⇒ **{t['wild_final']}**"
                ),
                inline=False
            )

    successes = sum(1 for t in tests if t["best_value"] >= T_value)
    total_raises = sum((t["best_value"] - T_value)//4 for t in tests if t["best_value"] >= T_value)
    embed.add_field(name="Resumo", value=f"{successes}/{count} sucesso(s) | ampliações totais: {total_raises}", inline=False)
    return embed

# ------------------------------------------------------------
# DANO (SEM selvagem) — com Dif/T, ampliações e até 2 termos (XdY + WdZ)
# ------------------------------------------------------------
def roll_damage_once(dice1_count: int, die1: int, dice2_count: int = 0, die2: int | None = None):
    """
    Rola até dois termos de dano:
      - Termo 1: dice1_count d die1
      - Termo 2 (opcional): dice2_count d die2
    Retorna:
      raw_sum,  # soma dos totais dos dados (sem mod)
      per_term  # lista de termos, cada um: {'die': die, 'totals': [..por dado..], 'rolls': [..por dado..]}
    """
    per_term = []
    # Termo 1
    t_totals_1, t_rolls_1 = [], []
    for _ in range(dice1_count or 1):
        t, rolls = roll_ace(die1)
        t_totals_1.append(t)
        t_rolls_1.append(rolls)
    per_term.append({"die": die1, "totals": t_totals_1, "rolls": t_rolls_1})

    # Termo 2 (se houver)
    if dice2_count and die2:
        t_totals_2, t_rolls_2 = [], []
        for _ in range(dice2_count):
            t, rolls = roll_ace(die2)
            t_totals_2.append(t)
            t_rolls_2.append(rolls)
        per_term.append({"die": die2, "totals": t_totals_2, "rolls": t_rolls_2})

    raw_sum = sum(sum(term["totals"]) for term in per_term)
    return raw_sum, per_term

def build_damage_group_embed(d1_count: int, d1: int, mod_all: int, T_value: int,
                             d2_count: int = 0, d2: int | None = None):
    raw_sum, per_term = roll_damage_once(d1_count or 1, d1, d2_count or 0, d2)
    final = raw_sum + mod_all

    # Título com EMOTE de dano + número
    title = f"{title_emote_damage(final, T_value)} · {final}"

    # Descrição: Dif, expressão e mod
    description = " | ".join(
        part for part in [
            f"Dif={T_value}",
            "Expr=" + (f"{d1_count or 1}d{d1}" + (f" + {d2_count}d{d2}" if d2_count and d2 else "")),
            f"mod {mod_all:+d}" if mod_all else None
        ] if part
    )

    embed = discord.Embed(title=title, description=description, color=color_for(final, T_value))

    # Mostrar termos (sem label 'Rolagens')
    for term in per_term:
        die = term["die"]
        lines = []
        for i, (tot, rolls) in enumerate(zip(term["totals"], term["rolls"]), start=1):
            lines.append(f"#{i}: {fmt_rolls(rolls, die)} ⇒ **{tot}**")
        embed.add_field(name=f"d{die}", value="\n".join(lines), inline=False)

    # Total (soma e final)
    embed.add_field(name="Total", value=f"Soma: **{raw_sum}** ⇒ Final: **{final}**", inline=False)

    # Resultado (ampliações)
    _, raises = assess(final, T_value)
    embed.add_field(name="Resultado", value=f"Final: **{final}**  |  Ampliações: **{raises}**", inline=False)
    return embed

def build_damage_individuals_embed(instances: int, d1_count: int, d1: int, mod_all: int, T_value: int,
                                   d2_count: int = 0, d2: int | None = None):
    instances = max(1, instances)
    d1_count = d1_count or 1

    results = []
    for _ in range(instances):
        raw_sum, per_term = roll_damage_once(d1_count, d1, d2_count or 0, d2)
        final = raw_sum + mod_all
        results.append({"raw_sum": raw_sum, "final": final, "per_term": per_term})

    # Título: tokens por instância (emote + número), separados por |
    tokens = [f"{title_emote_damage(r['final'], T_value)} · {r['final']}" for r in results]
    title = " | ".join(tokens)
    if len(title) > 250:
        title = title[:247] + "…"

    # Descrição
    expr = f"{d1_count}d{d1}" + (f" + {d2_count}d{d2}" if d2_count and d2 else "")
    desc = f"Dif={T_value} | Expr={expr}" + (f" | mod {mod_all:+d}" if mod_all else "")
    # cor baseada no primeiro (apenas estética)
    embed = discord.Embed(title=title, description=desc, color=color_for(results[0]['final'], T_value))

    if instances > 10:
        lines = []
        for i, r in enumerate(results, start=1):
            _, raises = assess(r["final"], T_value)
            lines.append(f"#{i}: Soma **{r['raw_sum']}** ⇒ Final **{r['final']}** | Ampliações: **{raises}**")
        chunk = ""
        block_idx = 1
        for line in lines:
            if len(chunk) + len(line) + 1 > 1000:
                embed.add_field(name=f"Danos (parte {block_idx})", value=chunk, inline=False)
                block_idx += 1
                chunk = ""
            chunk += (line + "\n")
        if chunk:
            embed.add_field(name=f"Danos{'' if block_idx==1 else f' (parte {block_idx})'}", value=chunk, inline=False)
    else:
        # Um field por instância
        for i, r in enumerate(results, start=1):
            lines = [f"• #{i}"]
            for j, term in enumerate(r["per_term"], start=1):
                die = term["die"]
                for k, (tot, rolls) in enumerate(zip(term["totals"], term["rolls"]), start=1):
                    lines.append(f"  - d{die} #{k}: {fmt_rolls(rolls, die)} ⇒ **{tot}**")
            _, raises = assess(r["final"], T_value)
            lines.append(f"  - Soma: **{r['raw_sum']}** ⇒ Final: **{r['final']}**  |  Ampliações: **{raises}**")
            embed.add_field(name=f"Dano #{i}", value="\n".join(lines), inline=False)

    return embed

# ------------------------------------------------------------
# HELP
# ------------------------------------------------------------
def build_help_embed():
    e = discord.Embed(
        title="Comandos Disponíveis",
        description="Sintaxe resumida (maiúsculas/minúsculas tanto faz).",
        color=0x3498DB
    )
    e.add_field(
        name="Testes com Selvagem (modelo SWADE)",
        value=(
            "**Grupo (um selvagem p/ o conjunto):**\n"
            "`[N]s<lado> [±mod] [T<dif>] [DS<6|8|10|12>]`\n"
            "• Ex.: `s8`, `3s6 +2`, `2s10 T6`, `4s8 DS10`\n"
            "• Título mostra um token por teste (✅/❌/💀 e 🏅xN)\n"
            "• `DS` troca o dado selvagem só nesta jogada\n\n"
            "**Individuais (#):**\n"
            "`N#s<lado> [±mod] [T<dif>] [DS<6|8|10|12>]`\n"
            "• Ex.: `3#s10`, `5#s6 +1 T6 DS8`\n"
            "• Cada teste tem seu próprio selvagem"
        ),
        inline=False
    )
    e.add_field(
        name="Dano / Extras (SEM selvagem) — com Dif e 2 termos",
        value=(
            "**Uma rolagem:**\n"
            "`XdY [ + WdZ ] [±mod] [T<dif>]`\n"
            "• Ex.: `2d6 +1`, `d10 T6`, `2d6 + d8`, `3d8 + 2d6 -2 T5`\n"
            "_Obs.: o modificador aplica **uma vez no total**._\n\n"
            "**Várias rolagens:**\n"
            "`M#XdY [ + WdZ ] [±mod] [T<dif>]`\n"
            "• Ex.: `4#2d6 +1`, `3#(2d6 + d8) -2 T6`, `5#d12 T4`\n"
            "**Títulos de dano mostram emote e número** (ex.: `✅ 🏅x2 · 17`)."
        ),
        inline=False
    )
    e.add_field(
        name="Dicas",
        value=(
            "• `Dif` padrão é **4**; mude com `T<valor>`.\n"
            "• `DS<8|10|12>` muda o dado selvagem só na jogada (ignorado em dano).\n"
            "• Explosões aparecem **em negrito** dentro dos colchetes."
        ),
        inline=False
    )
    return e

# ------------------------------------------------------------
# BOT
# ------------------------------------------------------------
@client.event
async def on_ready():
    print(f"Logado como {client.user} (id: {client.user.id})")
    print("Uso rápido:")
    print("  • Teste grupo: [N]s<lado> [±mod] [T<dif>] [DS<6|8|10|12>]")
    print("  • Teste individuais: N#s<lado> [±mod] [T<dif>] [DS<6|8|10|12>]")
    print("  • Dano: XdY [+ WdZ] [±mod] [T<dif>]   |   M#XdY [+ WdZ] [±mod] [T<dif>]")
    print("  • !help para ver tudo")

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Help
    if message.content.strip().lower().startswith("!help"):
        await message.reply(embed=build_help_embed())
        return

    # Captura DS e remove do texto base (ignorado em dano)
    ds_match = DS_PATTERN.search(message.content)
    wild_size = int(ds_match.group(1)) if ds_match else WILD_DEFAULT
    content_base = DS_PATTERN.sub("", message.content).strip()

    # ---------------------------
    # DANO (SEM selvagem) — primeiro, para não conflitar com 's'
    # ---------------------------
    # Individuais de dano: M#XdY [+ WdZ] [±mod] [T<dif>]
    m_dmg_ind = DMG_PATTERN_INDIV.match(content_base)
    if m_dmg_ind:
        inst_str, d1c_str, d1_str, d2c_str, d2_str, mod_str, t_str = m_dmg_ind.groups()
        instances = int(inst_str)
        d1_count = int(d1c_str) if d1c_str else 1
        d1 = int(d1_str)
        d2_count = int(d2c_str) if d2c_str else 0
        d2 = int(d2_str) if d2_str else None
        if instances < 1 or instances > MAX_COUNT:
            await message.reply(f"❌ Quantidade inválida. Use 1 a {MAX_COUNT}.")
            return
        mod_all = parse_mod(mod_str)
        T_value = int(t_str) if t_str else T_DEFAULT
        embed = build_damage_individuals_embed(instances, d1_count, d1, mod_all, T_value, d2_count, d2)
        await message.reply(embed=embed)
        return

    # Dano simples: XdY [+ WdZ] [±mod] [T<dif>]
    m_dmg_grp = DMG_PATTERN_GROUP.match(content_base)
    if m_dmg_grp:
        d1c_str, d1_str, d2c_str, d2_str, mod_str, t_str = m_dmg_grp.groups()
        d1_count = int(d1c_str) if d1c_str else 1
        d1 = int(d1_str)
        d2_count = int(d2c_str) if d2c_str else 0
        d2 = int(d2_str) if d2_str else None
        mod_all = parse_mod(mod_str)
        T_value = int(t_str) if t_str else T_DEFAULT
        embed = build_damage_group_embed(d1_count, d1, mod_all, T_value, d2_count, d2)
        await message.reply(embed=embed)
        return

    # ---------------------------
    # TESTES COM SELVAGEM (existentes)
    # ---------------------------
    # Individuais (#): N#sX [±mod] [T] [DS]
    m_ind = CMD_PATTERN_INDIV.match(content_base)
    if m_ind:
        count_str, die_str, mod_str, t_str = m_ind.groups()
        count = int(count_str)
        die = int(die_str)
        if count < 1 or count > MAX_COUNT:
            await message.reply(f"❌ Quantidade inválida. Use 1 a {MAX_COUNT}.")
            return
        mod_all = parse_mod(mod_str)
        T_value = int(t_str) if t_str else T_DEFAULT

        tests = []
        for _ in range(count):
            trait_total, trait_rolls = roll_ace(die)
            wild_total,  wild_rolls  = roll_ace(wild_size)
            trait_final = trait_total + mod_all
            wild_final  = wild_total  + mod_all
            is_crit = (trait_rolls[0] == 1 and wild_rolls[0] == 1)
            if trait_final >= wild_final:
                best_name, best_value = f"d{die}", trait_final
            else:
                best_name, best_value = f"Curinga d{wild_size}", wild_final
            tests.append({
                "trait_total": trait_total, "trait_rolls": trait_rolls, "trait_final": trait_final,
                "wild_total": wild_total,   "wild_rolls":  wild_rolls,  "wild_final":  wild_final,
                "best_name": best_name, "best_value": best_value, "crit": is_crit
            })

        embed = build_individuals_embed(count, die, mod_all, T_value, wild_size, tests)
        await message.reply(embed=embed)
        return

    # Grupo: [N]sX [±mod] [T] [DS]
    m_grp = CMD_PATTERN_GROUP.match(content_base)
    if not m_grp:
        return

    count_str, die_str, mod_str, t_str = m_grp.groups()
    count = int(count_str) if count_str else 1
    die = int(die_str)
    if count < 1 or count > MAX_COUNT:
        await message.reply(f"❌ Quantidade inválida. Use 1 a {MAX_COUNT}.")
        return
    mod_all = parse_mod(mod_str)
    T_value = int(t_str) if t_str else T_DEFAULT

    wild_total, wild_rolls = roll_ace(wild_size)
    wild_final = wild_total + mod_all

    trait_results = []
    for i in range(count):
        t_total, t_rolls = roll_ace(die)
        t_final = t_total + mod_all
        trait_results.append({"idx": i + 1, "total": t_total, "rolls": t_rolls, "final": t_final})

    embed = build_group_embed(count, die, mod_all, T_value, wild_size,
                              wild_total, wild_rolls, wild_final, trait_results)
    await message.reply(embed=embed)

# ------------------------------------------------------------
if __name__ == "__main__":
    if not TOKEN or TOKEN == "COLOQUE_SEU_TOKEN_AQUI":
        raise SystemExit("Defina a variável de ambiente DISCORD_TOKEN com o token do bot.")
    client.run(TOKEN)

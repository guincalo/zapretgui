"""В каком порядке проверять стратегии.

1. Сначала те, что уже подтвердились у этого пользователя на этой цели
   (не больше ``HEAD_CONFIRMED``: остальные идут в общем порядке).
2. Дальше — «разнообразием»: стратегии собраны в семейства — главный приём
   (fake, multisplit, hostfakesplit, …) и вид обмана фильтра (md5, ttl,
   badseq, …). Добавки вроде ``syndata`` и ``send`` семейства не плодят.
   Семейства идут вперемешку, и большое получает больше мест, чем стратегия-
   одиночка: первые 30 проверок покрывают все основные приёмы, а не 16
   вариаций одной добавки.
3. В конец — те, что недавно не сработали на этой цели. Поэтому повторный
   «Быстрый» подбор сам берёт ещё не проверенные стратегии.

Если BlockCheck недавно выяснил, как ведёт себя фильтр (``habit`` — код из
``diagnostics.filter_habits``), внутри шагов 2 и 3 порядок уточняется: вперёд
идут приёмы, у которых на таком фильтре есть шанс, в конец — заведомо слабые.
Ничего не выбрасывается: подсказка меняет только очерёдность.

Стратегия «ничего не делать» (``pass``) в кандидаты не входит: подбор
использует её как контроль.
"""

from __future__ import annotations

import math
import re
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

_DESYNC_FUNCTION = re.compile(r"^--lua-desync=([A-Za-z0-9_]+)")
PASS_FUNCTION = "pass"
# Сколько помнить «не сработала»: сеть и DPI провайдера со временем меняются.
FAILED_MEMORY_SECONDS = 14 * 24 * 3600

MODE_BATCH_SIZES = {"quick": 30, "standard": 80}
# Сколько надёжных стратегий достаточно, чтобы закончить подбор: человеку нужна
# рабочая, а не все рабочие. «Все стратегии» проверяет каталог целиком.
MODE_STOP_AFTER = {"quick": 1, "standard": 3}
# Ни одна из пачки не подошла — подбор сам берёт следующую, но не дальше стольких.
MODE_AUTO_CONTINUE_LIMIT = {"quick": 80, "standard": 160}

# Метка стратегии в каталоге → очередь внутри своего приёма: проверенные авторами
# раньше, опытные и «осторожно» — позже. Ничего не выбрасывается.
_LABEL_RANK = {"stock": 0, "recommended": 0, "stable": 0, "": 1, "game": 1, "experimental": 2, "caution": 2}
# Автокопии стратегий из встроенных пресетов: метка «stock» стоит у них по
# происхождению, а не потому, что авторы их отобрали. Их место определяет
# популярность в пресетах, а не метка.
_AUTOCOPY_PREFIXES = ("stock_builtin_coverage_", "stock_missing_")
# Сколько подтверждённых раньше стратегий ставить в самое начало.
HEAD_CONFIRMED = 5

# Добавки: сами по себе фильтр не обманывают, а усиливают главный приём.
# ``dht_dn`` меняет только пакеты DHT — для сайтов, голоса и игр её нет.
MODIFIER_FUNCTIONS = frozenset({"send", "syndata", "pktmod", "drop", "dht_dn"})
# Вид обмана фильтра у подделок: от более редкого к более частому — у
# стратегии с несколькими видами семейство определяет самый редкий.
_FOOLING_CLASSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("md5", ("tcp_md5",)),
    ("badsum", ("badsum",)),
    ("datanoack", ("datanoack", "tcp_flags_unset")),
    ("ttl", ("ip_ttl=", "ip6_ttl=")),
    ("autottl", ("ip_autottl", "ip6_autottl")),
    ("badseq", ("tcp_seq",)),
    ("ack", ("tcp_ack",)),
    ("ts", ("tcp_ts",)),
    ("seqovl", ("seqovl",)),
)


@dataclass(frozen=True, slots=True)
class Candidate:
    strategy_id: str
    name: str
    args: str
    # Метка каталога (recommended, stock, experimental, caution) — пусто, если её нет.
    label: str = ""
    # На скольких сервисах стратегия стоит в готовых пресетах программы.
    usage: int = 0


def candidate_rank(candidate: Candidate) -> tuple[int, int]:
    """Чем меньше, тем раньше внутри своего приёма: метка каталога, потом популярность в пресетах."""
    label = str(candidate.label or "").lower()
    if candidate.strategy_id.startswith(_AUTOCOPY_PREFIXES):
        label = ""
    return _LABEL_RANK.get(label, 1), -int(candidate.usage or 0)


# Простое дробление пакета: куски идут подряд и ничем не прикрыты.
_PLAIN_SPLIT = frozenset({"multisplit", "multisplit_tls", "tls_multisplit_sni", "tls_split_gentle", "slowsplit"})
_DISORDER = frozenset({"multidisorder", "multidisorder_legacy", "fakeddisorder", "tls_disorder_gentle"})
_FAKES = frozenset({"fake", "fakedsplit", "fakeddisorder", "hostfakesplit", "hostfakesplit_multi", "syndata"})
_RECORDS = frozenset({"tlsrec"})
# Привычка фильтра → (каким приёмам дать дорогу, считать ли простое дробление слабым, пояснение для журнала).
_HABIT_PLANS: dict[str, tuple[frozenset[str], bool, str]] = {
    "tcp": (_PLAIN_SPLIT | _DISORDER, False, "фильтр не склеивает пакеты — сначала стратегии с дроблением"),
    "timer": (
        _DISORDER | _FAKES,
        True,
        "фильтр склеивает куски, пришедшие подряд, — сначала смена порядка кусков и подделки, простое дробление в конце",
    ),
    "records": (
        _RECORDS | _FAKES,
        True,
        "фильтр склеивает пакеты, но не записи TLS — сначала дробление записи и подделки, простое дробление в конце",
    ),
    "none": (
        _FAKES,
        True,
        "фильтр собирает приветствие целиком — сначала подделки и наложение кусков, простое дробление в конце",
    ),
}


def habit_note(habit: str) -> str:
    """Пояснение для журнала подбора: как учтена проверка «Как работает фильтр». Пусто — подсказки нет."""
    plan = _HABIT_PLANS.get(str(habit or ""))
    return plan[2] if plan else ""


def habit_rank(candidate: Candidate, habit: str) -> int:
    """0 — приём с шансом на таком фильтре, 1 — обычный, 2 — заведомо слабый."""
    plan = _HABIT_PLANS.get(str(habit or ""))
    if plan is None:
        return 1
    prefer, plain_is_weak, _note = plan
    functions = set(technique_key(candidate))
    # Наложение кусков (seqovl) — отдельный приём, даже когда функция называется «дробление».
    overlap = "seqovl=" in candidate.args
    if plain_is_weak and functions and functions <= _PLAIN_SPLIT and not overlap:
        return 2
    if functions & prefer or (plain_is_weak and overlap):
        return 0
    return 1


def _by_habit(candidates: Sequence[Candidate], habit: str) -> list[Candidate]:
    """Группы по шансам, внутри каждой — приёмы по кругу."""
    ordered: list[Candidate] = []
    for rank in (0, 1, 2):
        ordered += interleave_by_technique([item for item in candidates if habit_rank(item, habit) == rank])
    return ordered


def desync_functions(args: str) -> tuple[str, ...]:
    names: list[str] = []
    for line in str(args or "").splitlines():
        match = _DESYNC_FUNCTION.match(line.strip())
        if match:
            names.append(match.group(1).lower())
    return tuple(names)


def is_pass_strategy(candidate: Candidate) -> bool:
    functions = set(desync_functions(candidate.args))
    return not functions or functions == {PASS_FUNCTION}


def technique_key(candidate: Candidate) -> tuple[str, ...]:
    """Все функции обхода стратегии без повторов и порядка."""
    return tuple(sorted(set(desync_functions(candidate.args)) - {PASS_FUNCTION}))


def main_technique(candidate: Candidate) -> str:
    """Главный приём: функции без добавок (``MODIFIER_FUNCTIONS``)."""
    functions = set(technique_key(candidate))
    main = functions - MODIFIER_FUNCTIONS or functions
    return "+".join(sorted(main))


def fooling_class(candidate: Candidate) -> str:
    args = candidate.args
    for name, markers in _FOOLING_CLASSES:
        if any(marker in args for marker in markers):
            return name
    return "none"


def family_key(candidate: Candidate) -> tuple[str, str]:
    """Семейство: главный приём и вид обмана фильтра."""
    return main_technique(candidate), fooling_class(candidate)


def _round_robin(queues: list[list[Candidate]]) -> list[Candidate]:
    ordered: list[Candidate] = []
    while queues:
        next_round: list[list[Candidate]] = []
        for queue in queues:
            ordered.append(queue.pop(0))
            if queue:
                next_round.append(queue)
        queues = next_round
    return ordered


def _family_queue(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Стратегии одного главного приёма: виды обмана по кругу, внутри — по метке и популярности."""
    subgroups: OrderedDict[str, list[Candidate]] = OrderedDict()
    for candidate in candidates:
        subgroups.setdefault(fooling_class(candidate), []).append(candidate)
    queues = sorted((sorted(group, key=candidate_rank) for group in subgroups.values()), key=lambda group: candidate_rank(group[0]))
    return _round_robin(queues)


def interleave_by_technique(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Главные приёмы вперемешку, в доле по размеру семейства.

    У приёма из N стратегий вес √N (а у отмеченного «опытным» — вдвое меньше):
    очередное место получает приём с наименьшим «занято / вес». Так fake из
    ~90 вариантов получает несколько мест в первых 30, а стратегия-одиночка —
    одно, но не раньше основных приёмов. Сортировка устойчивая: при равенстве
    решают метка, популярность и порядок каталога.
    """
    groups: OrderedDict[str, list[Candidate]] = OrderedDict()
    for candidate in candidates:
        groups.setdefault(main_technique(candidate), []).append(candidate)
    queues: list[list[Candidate]] = []
    weights: list[float] = []
    for group in groups.values():
        queue = _family_queue(group)
        queues.append(queue)
        best = min(candidate_rank(item)[0] for item in queue)
        weights.append(math.sqrt(len(queue)) * (1.0 if best <= 1 else 0.5))
    taken = [0] * len(queues)
    ordered: list[Candidate] = []
    total = sum(len(queue) for queue in queues)
    while len(ordered) < total:
        index = min(
            (i for i, queue in enumerate(queues) if queue),
            key=lambda i: ((taken[i] + 0.5) / weights[i], candidate_rank(queues[i][0]), i),
        )
        ordered.append(queues[index].pop(0))
        taken[index] += 1
    return ordered


def family_siblings(ordered: Sequence[Candidate], winner: Candidate, *, limit: int = 4) -> list[Candidate]:
    """Ещё не проверенные стратегии того же семейства, что и открывшая цель.

    Соседи удачной стратегии (тот же приём, тот же вид обмана, другие
    параметры) открывают цель чаще случайных — так ищут и blockcheck2, и b4.
    """
    key = family_key(winner)
    return [item for item in ordered if item.strategy_id != winner.strategy_id and family_key(item) == key][: max(0, limit)]


def order_candidates(
    candidates: Iterable[Candidate],
    *,
    confirmed_ids: Sequence[str] = (),
    failed_at: Mapping[str, float] | None = None,
    now: float = 0.0,
    habit: str = "",
) -> list[Candidate]:
    pool = [candidate for candidate in candidates if not is_pass_strategy(candidate)]
    by_id = {candidate.strategy_id: candidate for candidate in pool}
    failed_at = failed_at or {}
    # Подтверждённая, а потом не сработавшая стратегия в начало больше не идёт:
    # история записывает провал после подтверждения, когда провайдер сменил фильтр.
    first = [
        by_id[strategy_id]
        for strategy_id in dict.fromkeys(confirmed_ids)
        if strategy_id in by_id and not failed_at.get(strategy_id)
    ][:HEAD_CONFIRMED]
    taken = {candidate.strategy_id for candidate in first}

    fresh: list[Candidate] = []
    recently_failed: list[tuple[float, Candidate]] = []
    for candidate in pool:
        if candidate.strategy_id in taken:
            continue
        failed_time = float(failed_at.get(candidate.strategy_id, 0.0) or 0.0)
        if failed_time and now - failed_time < FAILED_MEMORY_SECONDS:
            recently_failed.append((failed_time, candidate))
        else:
            fresh.append(candidate)
    # Давно проверенные раньше, чем проверенные только что.
    recently_failed.sort(key=lambda item: item[0])
    return [
        *first,
        *_by_habit(fresh, habit),
        *_by_habit([candidate for _time, candidate in recently_failed], habit),
    ]


def batch_for_mode(ordered: Sequence[Candidate], mode: str) -> list[Candidate]:
    size = MODE_BATCH_SIZES.get(str(mode or "").strip().lower())
    return list(ordered) if size is None else list(ordered[:size])


def stop_after_for_mode(mode: str) -> int:
    """Сколько надёжных стратегий хватит; 0 — проверять пачку до конца."""
    return MODE_STOP_AFTER.get(str(mode or "").strip().lower(), 0)


def continuation(ordered: Sequence[Candidate], mode: str, tested: int) -> list[Candidate]:
    """Следующая пачка, если в проверенных ничего не подошло. Пусто — продолжать некуда."""
    key = str(mode or "").strip().lower()
    size, limit = MODE_BATCH_SIZES.get(key), MODE_AUTO_CONTINUE_LIMIT.get(key)
    if size is None or limit is None or tested >= limit:
        return []
    return list(ordered[tested : min(tested + size, limit)])

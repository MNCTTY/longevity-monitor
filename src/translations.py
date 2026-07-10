"""Русские названия теорий и формулировки посылок для отображения.

Кураторские данные на английском; здесь — словари переводов, которыми
заполняются колонки theories.name_ru / premises.text_ru. Рендер (graph.py,
digest.py) берёт русский вариант через COALESCE(name_ru, name).

Применяется командой `translate` (src.cli). Ключи — theory_id / premise_id.
"""
from . import knowledge

THEORY_RU = {
    "th:cell-senescence": "Клеточное старение",
    "th:cellular-senescense": "Клеточное старение (вар.)",
    "th:senescence": "Старение (сенесценция)",
    "th:antagonistic-pleiotropy": "Антагонистическая плейотропия",
    "th:programmed-aging": "Теория программируемого старения",
    "th:epigenetic-clocks": "Теория эпигенетических часов",
    "th:evolutionary": "Эволюционная теория старения",
    "th:oxidative-damage-accumulation": "Накопление окислительных повреждений",
    "th:every-type": "Обзор всех теорий старения",
    "th:developmental": "Онтогенетическая теория старения",
    "th:mutation-accumulation": "Накопление мутаций",
    "th:hyperfunction": "Теория гиперфункции",
    "th:mitochondrial-loss": "Митохондриальная теория (потеря функции)",
    "th:natural-selection": "Теория естественного отбора",
    "th:disposable-soma": "Теория одноразовой сомы",
    "th:disposable-soma-ds": "Одноразовая сома (вар.)",
    "th:somatic-mutations": "Теория соматических мутаций",
    "th:inflammaging": "Инфламэйджинг (воспалительное старение)",
    "th:telomere": "Теломерная теория",
    "th:damage-accumulation": "Накопление повреждений",
    "th:dissipation": "Диссипативная теория старения",
    "th:general-dissipation": "Общая диссипативная теория",
    "th:weismann-s": "Гипотеза Вейсмана",
    "th:stohastic-aging": "Стохастическое старение",
    "th:entropic": "Энтропийная теория",
    "th:reproductive-cell-cycle": "Теория репродуктивно-клеточного цикла",
    "th:error": "Теория ошибок (катастрофа ошибок)",
    "th:wear-and-tear": "Теория «износа»",
    "th:physiological-dysregulation": "Физиологическая дисрегуляция",
    "th:redox": "Редокс-теория старения",
    "th:rate-of-living": "Теория «скорости жизни»",
    "th:immunosenescence": "Иммуносенесценция",
    "th:codon-restriction": "Теория кодон-рестрикции",
    "th:genetic-load": "Теория генетического груза",
    "th:pathogen-control": "Теория контроля патогенов",
    "th:loss-of-heterochromatin": "Теория потери гетерохроматина",
    "th:brain-body-energy-conservation": "Модель «мозг–тело: сохранение энергии»",
    "th:information": "Информационная теория старения",
    "th:loss-of-morphostatic-information": "Потеря морфостатической информации",
    "th:metabolaging-inflammaging": "Метаболэйджинг / инфламэйджинг",
    "th:danaid": "Данаидная теория старения",
    "th:tumor-suppression": "Теория подавления опухолей",
    "th:immunologic": "Иммунологическая теория старения",
    "th:ergodic": "Эргодическая теория",
    "th:redundant-message": "Теория избыточного сообщения",
}

PREMISE_RU = {
    "prem:00c64a57f8f6cb66": "Вмешательства способны сдвигать кривые заболеваемости и смертности",
    "prem:13c6bbe786685770": "Некоторые виды демонстрируют пренебрежимо малое старение",
    "prem:20f8d175d21db0f8": "Последовательность ДНК определяет продолжительность жизни",
    "prem:22b30183943f4529": "Старение фактически начинается после прекращения роста",
    "prem:32cdfc32795490ea": "У старения общие причины для всех билатеральных организмов",
    "prem:489be6ba2547f1cc": "«Тень отбора»: сила естественного отбора падает после репродуктивной зрелости у высших видов",
    "prem:5ae8fbcecdc3dbf8": "Смертность монотонно растёт с возрастом (с некоторыми исключениями)",
    "prem:9ed3334b16ac3e35": "У эусоциальных видов велики внутривидовые различия в продолжительности жизни",
    "prem:a07dbb2fe2b60e36": "У старения есть чёткий фенотипический паттерн (по крайней мере у млекопитающих)",
    "prem:b23e842e40731de2": "Некоторые виды демонстрируют феноптоз (программируемое старение и смерть, лосось)",
    "prem:dee2027653fa1777": "Некоторые виды приостанавливают старение до достижения зрелости (аксолотль)",
    "prem:ecf1b31c0d8eab52": "Масса вида (внутри клады) коррелирует с максимальной продолжительностью жизни",
}


def apply(con):
    """Записать русские названия в колонки name_ru / text_ru."""
    knowledge.ensure_schema(con)
    nt = np = 0
    for tid, ru in THEORY_RU.items():
        nt += con.execute("UPDATE theories SET name_ru=? WHERE theory_id=?", (ru, tid)).rowcount
    for pid, ru in PREMISE_RU.items():
        np += con.execute("UPDATE premises SET text_ru=? WHERE premise_id=?", (ru, pid)).rowcount
    con.commit()
    return {"theories": nt, "premises": np}

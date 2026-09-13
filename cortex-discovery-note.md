# Изолированный Desktop: конфигурация модели

Конфигурация изолированного Desktop-запуска формируется в
`scripts/cortex-desktop-dev`, функция `live_test_config(source)` (строки
192–223). Она переписывает конфигурацию так, чтобы задать верхнеуровневые
`model = "gpt-5.6-luna"` и `model_reasoning_effort = "high"`, добавить live
developer policy и установить `[agents].default_subagent_model` на Luna.

Прямой вызывающий код — функция `configure_workspace_network()` в том же файле
(строки 226–260): она читает изолированный `~/.cortex-dev/.codex/config.toml`,
вызывает `live_test_config(source)`, включает loopback `network_access` и
атомарно записывает результат обратно.

Кто вызывает `configure_workspace_network()`: функция `main()` в
`scripts/cortex-desktop-dev` (ветка `start`, строки 4575–4703; вызов на строке
4604). Скриптовая точка входа — `if __name__=='__main__': main()` (строки
4705–4708). Таким образом, цепочка вызовов при `start`:

`cortex-desktop-dev` → `main()` → `configure_workspace_network()` →
`live_test_config(source)`.

Основание: опубликованный read-only отчёт Cortex
`r_ebdffd7c71a5` (профиль `cortex:worker-explorer`); проверка исходника
подтвердила определения и вызов (`nl -ba ... | sed -n ...`). Граф сообщил
косвенную ancestry-цепочку `main()` → `configure_workspace_network()` →
`live_test_config(source)`; дополнительных прямых вызовов не найдено.
Live Desktop-запуск не выполнялся, поэтому это карта исходного кода, а не
подтверждение эффективного runtime-конфига; будущие сгенерированные или
неиндексированные вызовы этим bounded-исследованием не исключаются.

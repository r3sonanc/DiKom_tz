# Измеримые критерии финальной приёмки

Проверяются две штатные карты (`normal`: 1–3 робота, `hardcore`: 1–5).
`hardcore` в интерфейсе называется «Узкие коридоры».
Глобальная остановка при потере связи, согласованные раунды и геометрические
проверки сохраняются. Времена ниже относятся к модельным часам и первому
выполнившемуся шагу; остановка самого процесса ОС не имеет гарантированного
времени реакции по настенным часам.

| Приоритет | Опыт | Измеримый результат |
|---|---|---|
| P0 | Прекратить продление автоматического движения | На первом физическом шаге после истечения lease 0,9 с линейная и угловая скорости равны нулю; для ручного режима lease 0,35 с |
| P0 | Потерять связь посреди разрешённого ребра | После возраста принятой телеметрии >1,5 с статус OFFLINE; на ближайшем тике парк получает остановки, новых move/manual нет |
| P0 | Продолжать тики без сообщений | Оба конца последнего разрешённого ребра остаются в резервах даже после десяти heartbeat-тайм-аутов |
| P0 | Вернуть свежую телеметрию | Появляется RECOVERING; ONLINE разрешается после проверки запуска, положения, команды, остановки и отсутствия ошибки |
| P0 | Прислать неверную позу, чужой активный приказ или старый seq/boot_id | Движение не возобновляется; старые пакеты не обновляют heartbeat и не снимают резервы |
| P0 | Перезапустить робота во время перехода | Старая область остаётся защищена, парк на паузе; безопасная свежая поза даёт recovery_ready, продолжение требует явного resume |
| P0 | Повторить уже завершённую команду с прежними ID/ревизией | Выполнение не запускается заново, новая миссия не появляется, число завершений не увеличивается повторно |
| P0 | Два робота претендуют на узкий проход | В каждом разрешённом раунде ресурсы путей не пересекаются; геометрический oracle не обнаруживает перекрытия корпусов |
| P1 | Одно событие потери/восстановления, затем 100 одинаковых пакетов и тиков | Каждый соответствующий счётчик и переходная запись увеличиваются ровно один раз |
| P1 | Появление ошибки, повтор, подтверждённый сброс | Одна ROBOT_ERROR на новую ошибку и одна ERROR_CLEARED после принятого состояния без ошибки |
| P1 | Проверить stdout и журнал UI | События содержат UTC, уровень, robot_id, код и причину; прежние поля time/level/message присутствуют |
| P1 | Один робот OFFLINE, другие продолжают присылать состояния | `/api/health`: HTTP 200, ok=true, корректные fleet.online/offline/recovering/errors и safety_stop=true |
| P1 | Все роботы молчат >3 с либо транспорт/контроллер остановлен | `/api/health`: HTTP 503, ok=false с диагностикой самого сервиса; сохранены world_id и transport |
| P1 | Планирование произошло / повтор подавлен | Последняя длительность и sum/count обновляются только при настоящей попытке построения плана, значения конечны и неотрицательны |
| P2 | Push или PR в main | GitHub Actions на Ubuntu 24.04 собирает test-stage, импортирует Python/ROS-модули, выполняет весь pytest и сохраняет результат |
| P2 | Открыть UI при потере связи робота | Верхний индикатор WebSocket остаётся независимым; робот показывает OFFLINE/RECOVERING, позиция явно помечена как последняя известная |
| P2 | Проверить дизайн и совместимость | Сохранены favicon, фирменные цвета, действующие HTTP-маршруты и прежние поля API; новые поля добавлены без удаления старых |
| P2 | Повторить benchmark | Записаны median/p95 тика, длительности планирования, JSON-кадры для 1/10/50 клиентов, Python-выделения и завершённые задачи обеих карт |
| P2 | Сравнить производительность | Те же 600 модельных секунд, seeds 0/1/2 и три повтора; любая разница задач или существенное замедление объяснены в VALIDATION.md, безопасность важнее микрооптимизации |

Начальное ожидание телеметрии не считается потерей уже установленной связи.
LINK_RECOVERED обозначает приём свежего состояния после OFFLINE; разрешение
движения — отдельная проверка, поэтому RECOVERING не равен ONLINE.
Новые счётчики, как прежние метрики симуляции, относятся к текущему `world_id`
и обнуляются при создании новой сцены. Повторы и отвергнутые сообщения не являются
новыми событиями. `online` в health означает свежесть телеметрии и включает
свежие RECOVERING; их число также показывается отдельно.

Конечный набор тестов подтверждает конкретные сценарии и инварианты модели,
а не даёт гарантии для произвольного оборудования и всех вещественных входов.
Предпосылки геометрической безопасности сохраняются в [SAFETY.md](SAFETY.md).
Фактические команды, результаты и ограничения измерений — в [VALIDATION.md](VALIDATION.md).

## Где проверяется P0/P1

Имена ниже — реальные тесты. Файлы: [recovery](../tests/test_recovery.py),
[observability](../tests/test_observability.py), [safety](../tests/test_safety.py).

| Критерий | Проверка |
|---|---|
| Lease, оба конца ребра, резервы после повторных timeout, глобальная остановка | `test_loss_in_an_edge_keeps_both_endpoints_and_local_lease_stops_motion`; живой `live_smoke.py --ros` |
| RECOVERING до подтверждения остановки | `test_fresh_moving_state_remains_recovering_until_stopped_state_is_confirmed` |
| Неверная поза / чужой приказ блокируют resume | `test_contradictory_pose_keeps_old_resources_and_blocks_resume`, `test_unknown_active_command_cannot_restart_a_transition` |
| Старые seq/boot и задержанные возрастающие seq не снимают резервы | `test_old_or_duplicate_state_does_not_restore_the_link`, `test_delayed_increasing_sequences_cannot_restore_link_or_release_resources` |
| Возраст состояния ≤0,5 с, будущее ≤0,1 с; heartbeat от времени источника | `test_source_timestamp_is_finite_bounded_and_does_not_run_backwards`, `test_delivery_delay_does_not_extend_source_heartbeat` |
| Reboot: резервы, та же миссия, явный resume | `test_reboot_in_transition_preserves_mission_and_requires_explicit_resume`; `ros_recovery_smoke.py` через DDS |
| Старое подтверждение не завершает новый приказ | `test_reboot_arrival_confirmation_cannot_complete_a_different_permit` |
| Завершённая команда не выполняется повторно | `test_a_completed_move_is_not_executed_again_after_duplicate_delivery`, `test_completed_transition_after_loss_does_not_create_another_mission_or_permit` |
| Узкий проход, независимая геометрия | Полный `test_safety.py`, включая перебор 288 назначений |
| Потеря/возврат учитываются один раз; startup не потеря | `test_outage_counts_each_link_once_and_one_stop_for_whole_fleet`, `test_startup_and_snapshot_reads_do_not_create_outage_events`, `test_recovery_counter_counts_the_first_fresh_packet_once_not_duplicates` |
| Ошибка и подтверждённый сброс без дублей | `test_error_transitions_are_counted_once_and_clear_requires_confirmed_state`, `test_duplicate_error_telemetry_does_not_duplicate_logs_or_metrics` |
| UTC-логи, поля UI, INFO/WARNING перехватываются | `test_structured_link_events_and_counters_do_not_repeat_on_each_tick`, `test_log_records_keep_utc_fields_on_one_line`, `test_info_events_reach_process_handlers_once` |
| Формат метрик, актуальный offline, реальное время планирования | `test_metrics_expose_new_counters_and_a_live_offline_gauge`, `test_planning_duration_covers_the_whole_attempt_and_failure_log_is_deduplicated` |
| Health 200 при частичной потере, 503 после отсутствия всех ≥3 с | `test_partial_fleet_health_is_independent_from_service_status`, `test_health_returns_503_after_all_current_world_telemetry_goes_stale`; оба случая в `live_smoke.py` |
| Контроллер/транспорт не работают, начальной телеметрии нет | `test_health_checks_controller_and_transport_lifecycle`, `test_health_is_503_until_initial_world_has_telemetry` |

Допуск свежести предполагает общий монотонный clock domain текущего симулятора.
Для разных физических хостов нужны отдельные требования синхронизации и протокола;
рост `seq` сам по себе не подтверждает актуальность положения. Критерии не обещают
реакцию в заданные миллисекунды при остановке процесса ОС.

# Limit training to race-goal schemes

训练板块第一版只为明确目标赛事创建 Training Goal 和 Training Scheme；没有目标赛事时只展示已有活动、报告和建立目标入口，不生成滚动四周方案，也不维护 `continuous_running`、`race_preparation` 或 `recovery_transition` 教练模式。目标日期当天结束后自动终止当前方案：已匹配赛事活动时 completed，否则以 `target_date_reached` archived；取消或主动结束同样回到无方案状态。赛后结果进入报告，不创建恢复过渡排课。该范围放弃无赛事长期陪伴和恢复过渡排课，以换取一条容易理解、实现和验证的赛事备赛主线。

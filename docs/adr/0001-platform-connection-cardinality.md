# Keep platform connections conceptually one-to-many

neurun 第一版限制每个 neurun Account 最多拥有一个活跃 Platform Connection，以避免多平台活动去重、身份合并和冲突优先级过早进入注册范围；但领域关系明确保留为一个账号可拥有多个连接，代码与数据命名不得把 neurun Account 永久等同于单个 Platform Account。这样当前交付保持简单，同时避免未来多平台接入必须重定义账号身份。

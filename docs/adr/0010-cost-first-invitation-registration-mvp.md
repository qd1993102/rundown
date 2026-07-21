# Keep invitation registration cost-first

neurun 当前没有正式用户，注册 MVP 只实现：JSON 持久化的系统随机、明文、单次、不过期 Invitation；昵称、格式合法且唯一的 Login Email、密码注册与登录；固定 30 天 Cookie；每个账号一个 Platform Connection；服务器本地邀请码管理 CLI。Recovery Code、密码找回与修改、账号删除、换绑、旧数据迁移、SQLite 认证库和多设备可撤销 Session 全部延后，等真实用户需求出现再设计；ADR-0002、0004、0005 和 0007 因此被本决策取代。

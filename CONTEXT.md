# neurun Domain Language

neurun 为运动者提供训练数据同步、训练记忆与 AI 教练能力。本词汇表定义产品讨论和代码设计中使用的统一领域语言。

## Access and Registration

**Invitation**:
一份由系统随机生成、不预先绑定邮箱、仅允许创建一个 neurun 账号的一次性 bearer 注册资格；新 Invitation 使用 6 位无歧义大写字母与数字，已发布的旧版长码继续有效；由首个成功使用者获得，只有账号创建成功后才算被使用，并在使用或管理员停用前持续有效。
_Avoid_: Shared invite, email-bound invite, campaign code, promo code

## Accounts and Connections

**neurun Account**:
运动者登录 neurun 并拥有其训练数据与设置的应用身份；当前最多拥有一个活跃 Platform Connection，但领域关系允许未来扩展为多个。
_Avoid_: User key, provider account, Garmin account

**Login Email**:
经格式校验并在 neurun 内唯一的账号登录标识；第一版创建后不可修改且不验证邮箱归属，因此它不是可信联系方式或账号所有权证明。
_Avoid_: Verified email, contact email, recovery email

**Nickname**:
可重复且可修改的账号展示名称，不参与登录、唯一性判断或账号所有权证明。
_Avoid_: Username, account ID, Login Email

**Platform Account**:
运动者在 Garmin、Coros、Huawei 等外部运动平台上的身份，与 neurun Account 相互独立。
_Avoid_: neurun account, application account

**Platform Connection**:
一个 neurun Account 获得持续读取某个 Platform Account 数据授权的关系；第一版每个 neurun Account 最多一个活跃连接。
_Avoid_: Login, provider field, token directory

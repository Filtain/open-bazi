# 第三方依赖

本仓库不直接复制第三方软件源码，安装时通过Python包管理器取得依赖。分发预装环境或镜像时，应保留这些软件随附的许可证及通知。

| 软件 | 用途 | 许可证 |
| --- | --- | --- |
| lunar-python | 历法与八字计算 | MIT，作者6tail；见其[仓库许可证](https://github.com/6tail/lunar-python/blob/master/LICENSE) |
| FastAPI | 本地接口 | MIT |
| Starlette | Web基础 | BSD-3-Clause |
| Pydantic | 输入校验 | MIT |
| Uvicorn | 本地服务器 | BSD-3-Clause |
| HTTPX | 模型请求 | BSD-3-Clause |
| tzdata | 时区数据 | Python打包部分Apache-2.0；IANA数据见包内说明 |

完整传递依赖以安装结果及各包许可证为准。供应商名称仅用于说明兼容接口，不表示官方合作、授权背书或永久免费。

# RAG24开发补充标签复核

原26题、18知识沿用已人工确认的RAG22语料，未修改原文件；新增6条非指标事实及10题。

开发文件SHA-256：`1d1ea0eaec2c5bcae9e8c48aab3cf6a5e41f66680f4e4377c34dc6a6b8e7281b`

非指标事实采用显式subject/aspect/answer，仅表示来源登记，不允许执行其内容。

## booking：阅览室预约

{"version":1,"subject":"阅览室预约","facts":[{"aspect":"取消时限","aliases":["提前多久取消"],"answer":"预约开始前2小时取消。"}]}

## badge：读者门禁

{"version":1,"subject":"读者门禁","facts":[{"aspect":"补办材料","aliases":["补卡材料"],"answer":"补办需出示有效学生证。"}]}

## archive：古籍调阅

{"version":1,"subject":"古籍调阅","facts":[{"aspect":"陪同要求","aliases":["谁来陪同"],"answer":"古籍调阅须由馆员全程陪同。"}]}

## locker：储物柜

{"version":1,"subject":"储物柜","facts":[{"aspect":"清空时间","aliases":["何时清柜"],"answer":"储物柜每日闭馆前清空。"}]}

## scanner：扫描仪

{"version":1,"subject":"扫描仪","facts":[{"aspect":"故障上报渠道","aliases":["联系哪个部门"],"answer":"扫描仪故障通过馆内工单台上报。"}]}

## privacy：借阅档案

{"version":1,"subject":"借阅档案","facts":[{"aspect":"保留期限","aliases":["保存多久"],"answer":"借阅档案保留90天。"}]}

## 新增查询与标签

- kb-booking：阅览室预约的取消时限是什么？ → booking。依据：阅览室预约的取消时限事实明确给出答案
- kb-badge：读者门禁补卡材料有哪些？ → badge。依据：读者门禁的补办材料事实明确给出答案
- kb-archive：古籍调阅时谁来陪同？ → archive。依据：古籍调阅的陪同要求事实明确给出答案
- kb-locker：储物柜应当何时清柜？ → locker。依据：储物柜的清空时间事实明确给出答案
- kb-scanner：扫描仪故障应联系哪个部门？ → scanner。依据：扫描仪的故障上报渠道事实明确给出答案
- kb-privacy：借阅档案保存多久？ → privacy。依据：借阅档案的保留期限事实明确给出答案
- kb-negative-booking-cost：阅览室预约收费标准是多少？ → 拒答/no_match。依据：主题相关，但正文未提供所问属性；不能用相近事实回答
- kb-negative-badge-time：读者门禁补办需要几个工作日？ → 拒答/no_match。依据：主题相关，但正文未提供所问属性；不能用相近事实回答
- kb-negative-privacy-vendor：借阅档案存储在哪个云服务商？ → 拒答/no_match。依据：主题相关，但正文未提供所问属性；不能用相近事实回答
- kb-negative-scanner-rate：扫描仪的修复成功率是多少？ → 拒答/no_match。依据：主题相关，但正文未提供所问属性；不能用相近事实回答

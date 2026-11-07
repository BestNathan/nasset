"use strict";
/* Shared metric semantics for every nasset Pages domain.
 * "Formula" must describe actual implementation or source data, never
 * a suggested trading signal. A missing/unsupported value remains "—". */
(function(root) {
  const specs = new Map();
  const esc = v => String(v ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;")
    .replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#39;");
  function define(labels, formula, meaning, boundary, source="") {
    for(const label of labels.split("|")) {
      if(specs.has(label))throw new Error("Duplicate metric definition: "+label);
      specs.set(label,{formula,meaning,boundary,source});
    }
  }
  // Volatility levels and the risk-neutral implied smile (not price forecasts).
  define("30D ATM IV|30D ATM","按期限对挂牌到期日的 ATM Mark IV 插值至 30 天；ATM 来自邻近远期执行价的有效挂牌 IV。","市场对近 30 天波动幅度的期权定价水平，较高代表波动率报价更贵。","仅在有效期限节点间插值；Mark IV ≠未来真实波动率。","Deribit Mark IV；nasset Black-76 插值");
  define("25Δ Risk Reversal","RR25 = 25Δ Call IV − 25Δ Put IV（30D 期限点）。","观察看涨翼与看跌翼相对昂贵程度；负值表示看跌翼 IV 较高。","相对定价而非涨跌概率、方向预测；不能用缺失翼部节点外推。");
  define("25Δ Butterfly","BF25 = (25Δ Call IV + 25Δ Put IV)/2 − ATM IV。","比较两侧尾部与平值的波动率溢价，辅助观察微笑曲率。","风险中性报价结构；不等于真实尾部事件概率。");
  define("DVOL","来自 Deribit 官方 DVOL 观察值；nasset 不重新计算指数。","Deribit 的近 30 日期权隐含波动率指数。","官方指数是外部输入，不应伪造本地 Black-76 公式。","Deribit DVOL");
  define("Hourly RV","令 rᵢ=ln(Pᵢ/Pᵢ₋₁)，RV = sample_std(rᵢ)×√(24×365)×100%。","根据连续真实小时价格收益观测估计的历史实现波动率。","至少 12 个连续 1H 收益样本；缺失时段不补零、不插值。");
  define("IV − RV","同一页面可比 30D ATM IV − 真实 1H 年化 RV，单位 vol points。","比较期权隐含波动率与近期实现波动率的相对差距。","两者观察窗与测量方法不同；差值不等于可实现的交易利润。");
  define("Options OI","OI 名义美元量 ≈ Σ(合约公开未平仓标的单位 q) × 当前参考指数价格。","观察挂牌期权市场的名义未平仓规模。","OI 只记录总未平仓量；无法识别投资者、净多空或做市商方向。");
  define("Gross Gamma / 1%|GEX / 1%","Gross Γ(+1% F) = Σ[q × F² × Γ × 0.01]，单位 USD Δ 变化。","把每份期权标的价格上移 1% 时 Delta 的模型敏感度按 OI 聚合，观察凸性规模。","全部 OI 假设多头、保留绝对敏感度；不是净做市商 GEX 或真实对冲流。");
  define("Vanna / 1 vol pt","Σ[q × F × |Vanna| × 0.01]；Vanna = ∂Δ/∂σ。","隐含波动率移动 1 个百分点时，模型美元 Delta 敏感度的总量。","OI 多头绝对量，不能推断真实净头寸方向。");
  define("Charm / 1 day","Σ[q × F × |Charm_day|]；Charm_day 依据 Black-76 计算日历时间衰减。","时间流逝一天时，期权 Delta 变化的美元名义敏感度。","假设价格/IV 固定的模型偏导，不是实际日度成交或对冲量。");
  define("Volga / (1vol pt)²","Σ[q × |Volga| × (0.01)² / 2]；Volga = ∂Vega/∂σ。","衡量 IV 改变一个百分点时的二阶波动率价格项。","只是一阶 Vega 之外的二阶近似，不是完整期权 P&L。");
  define("Vega / 1 vol pt","Σ[q × Vega × 0.01]；Vega=F φ(d₁)√T。","IV 移动 1 个百分点时的理论期权美元价格一阶敏感度。","假设远期、到期、持仓和折现不变；Gross OI 不等于净投资者仓位。");
  define("Put / Call OI","同一标的与结算市场：Σ Put OI / Σ Call OI。","观察看跌与看涨两侧未平仓量的相对规模。","不能由未平仓量直接推断买卖方向或市场多空立场。");
  define("Gamma Wall","argmax_K Σ₍同一 K 全到期合约₎[q × F² × Γ × 0.01]。","识别 Gross Gamma 模型敏感度最集中的行权价。","不是技术意义上的支撑/阻力或 Gamma Flip。");
  define("≤ 7D Gamma","100% × Σ(DTE≤7 天的 Gross Gamma) / Σ(全部 Gross Gamma)。","量化凸性敏感度集中在短期限的比例。","缺失 OI / IV 不视作零；不是临近到期的涨跌预测。");
  define("Gamma","Black-76：Γ = φ(d₁)/(F σ√T) = ∂Δ/∂F。","标的远期变化时，期权 Delta 的变化速度（二阶价格敏感度）。","对单份期权、单位远期价格定义；不含方向未知的市场净仓位。");
  define("Delta|Δ*","Black-76：Call Δ=N(d₁)，Put Δ=N(d₁)−1。","期权价格对标的远期价格的一阶敏感度。","非 ITM 概率的精确替代，也不是实际持仓方向。");
  define("Vanna","Black-76：Vanna = −φ(d₁)d₂/σ = ∂Δ/∂σ。","观察波动率变化对 Delta 的影响，常用于判断跨风险因子耦合。","每单位波动率小数变动定义；不要把 1 vol pt 当成 σ=1。");
  define("Volga","Black-76：Volga = Vega × d₁×d₂/σ。","衡量 Vega 随波动率变化的二阶敏感度。","单合约模型值；大幅波动率冲击需要重新定价。");
  define("Charm","Black-76：Charm_day = −φ(d₁)×(∂d₁/∂T)/365。","观察经过一个日历日，Delta 随时间发生的局部漂移。","固定远期和 IV；不代表实际持仓对冲交易。");
  define("Mark IV|MARK IV","Deribit 合约报价的 mark_iv 直接读取，单位 %；不是浏览器逆向求解。","市场标记期权隐含波动率，是曲面和模型 Greeks 的输入。","空报价不当成零；不同结算家庭分开比较。","Deribit options mark_iv");
  define("OI|Open Interest","Deribit 每合约公开 Open Interest 原值，按合约单位口径读取。","刻画市场未平仓合约规模与聚集位置。","不披露多空持仓者身份、做市商库存或交易方向。");
  define("IV surface · Expiry × Delta","对每个有效到期日按 Black-76 远期 Delta 定位 10P/25P/ATM/25C/10C Mark IV 节点；只有相邻有效节点才插值。","观察期限 × Delta 维度的隐含波动率曲面，识别期限和执行价相对报价。","缺值留空；不把稀疏曲面外推为完整市场报价。");
  define("ATM 期限结构","按 7/14/30/60/90/180 日目标期限对可用 ATM IV 作受限线性期限插值。","比较近期与远期期权的隐含波动率定价水平。","非收益率曲线；无有效括点时没有对应期限点。");
  define("偏斜与尾部溢价","RR25 = IV(25C)−IV(25P)；BF25 = [IV(25C)+IV(25P)]/2−ATM IV。","把波动率曲面的左右翼偏斜与曲率放在同一视图比较。","反映相对报价，而非事件发生概率。");
  define("IV vs Realized","把 30D ATM IV、官方 DVOL 与真实连续 1H 收益推导的 RV 并列比较。","区分隐含报价和已经实现的历史波动。","历史 RV、30D ATM 和 DVOL 不是同一种观察窗口或估计器。");
  define("OI-weighted gross Greeks","同一结算市场内按 OI q 聚合 Black-76 模型 Gamma/Vega/Vanna/Charm/Volga；各项单位和冲击大小见子指标。","描述市场公开 OI 在给定模型假设下的敏感度上限式压力尺度。","不能识别净做市商敞口与对冲方向。");
  define("Convexity concentration","按行权价及剩余期限汇总 OI 加权 Gross Gamma。","观察凸性集中于哪些行权价和到期日。","不是价格支撑线、压制位或净仓方向。");
  define("Gamma concentration · Strike","按 K 汇总各到期日 OI × F² × Γ × 0.01，并按绝对量排序。","识别凸性敏感度的行权价集中区。","OI long-all 模型压力指标，不代表做市商净 GEX。");
  define("OI cluster · Expiry","按相同到期日分别 Σ Call OI、Σ Put OI。","观察不同到期日的公开未平仓量构成。","不能据此区分真实看涨/看跌买方或卖方。");
  define("Gross Δ rehedge · Spot shocks","Σ[q×F×|Δ(F×(1+x))−Δ(F)|]，x∈{±2%,±5%,±10%}。","衡量固定 IV 下标的冲击时的总绝对 Delta 变化尺度。","不是可观察的对冲成交量；不考虑净多空抵消。");
  define("Second-order mechanics","Black-76 各导数：Γ=∂Δ/∂F；Vanna=∂Δ/∂σ；Volga=∂Vega/∂σ；Charm=日历时间下 Delta 漂移。","将跨价格、波动率、时间的敏感度按风险因子聚类。","偏导只针对给定模型状态成立。");

  // Futures and term basis; PAXG has no validated options in this dataset.
  define("参考指数|Reference spot","取匹配标的/报价货币的指数参考报价，保留原始时间戳和计价口径。","为可比期货基差与名义价值提供现货参考。","指数值不是交易所可直接成交的现货订单。");
  define("永续 / 期货|上市期货 / 永续","统计当前结算家庭中已上市期货/永续合约行数。","观察合约覆盖广度。","合约数量不能直接代表成交深度或流动性。");
  define("可比到期合约|Tradable maturities","计算可获得有效年化基差的到期合约数量，要求到期 ≥1 天且有匹配指数。","衡量可用于期货期限结构对比的有效节点数量。","不将永续资金费率混作交割合约基差。");
  define("近月年化基差|Front contract basis","最近有效交割合约：(Mark/同计价参考指数−1)×365/DTE_days×100%。","短端期货相对现货的隐含年化升贴水。","简单年化不等于可锁定收益；忽略资金、保证金、滑点与成本。");
  define("90D 年化基差|90D basis","从实际挂牌的有效期货年化基差期限节点向 90D 作受限期限插值。","估计可观察期限结构中约 90 天的年化基差水平。","不跨缺失区间外推，不混用 inverse/USDC 计价家庭。");
  define("期限观测时段","统计包含有效期限曲面的、来自真实归档的历史时间槽数量。","说明曲面覆盖了多少个可验证的观测时点。","不插值造出缺失时间槽；槽数不是连续时间保证。");
  define("基差节点","统计具有可比参考指数、足够剩余期限和合法年化基差的合约。","观察当前期货基差可用性。","无有效参考报价时保留缺值。");
  define("期权市场|风险面","该标的缺少可验证 Deribit 期权链，因此不计算 IV、模型 Greeks 或 OI 风险面。","表明市场覆盖的边界，而不是填入模拟数据。","PAXG 在当前归档只覆盖上市期货/永续。");
  define("Annualized forward basis","简单年化基差：100%×(期货 Mark/匹配参考指数−1)×365/剩余天数。","对照不同交割日的期货相对现货升贴水。","不是套利净收益；保留资金费、交易费、保证金等成本缺口。");
  define("Forward market snapshot","读取指定结算家庭的最新 Mark、参考指数、剩余期限及可比基差节点。","分开观察 inverse 与 linear 期货市场结构。","永续不是具有确定到期日的交割合约。");
  define("Basis surface · Observation × Tenor","每个已验证历史时点上，在有效到期日之间插值 7/30/60/90/180D 年化基差。","观察期限结构随时间变化，而非单一时点。","仅真实时点和有效期限括点；不外推。");

  // Dividend equity research.
  define("2025已实施股息率","FY2025 已实施税前每股现金股息 / 最新A股收盘价 ×100%。","历史财年的已实现现金股息相对当前市场价的比率。","未实施派息不计入；当前股价变化会改变比率。");
  define("分红CAGR (2015—25)","(FY2025 可比每股现金股息 / FY2015 可比每股现金股息)^(1/10)−1。","衡量完整十年历史现金股息的复合增长。","仅有效完整可比系列计算；包含特别分红时需明确口径。");
  define("已观测减息","在有效相邻财年可比每股股息配对中，后一年低于前一年的次数。","历史上现金股息出现过多少次下降。","不是未来减息发生概率；缺失年份不能按不减息处理。");
  define("最大回撤","max_drawdown = min_t [P_t/max_{s≤t}(P_s)−1]。","观察历史最不利的峰谷跌幅。","取决于所选区间和复权行情，不代表极端情景上限。");
  define("当前股价","最新可验证 A 股收盘价，单位人民币。","用于估值和股息率分母的市场观察价。","注意行情日期，非实时可成交价格。");
  define("近12个月现金股息率","最近365天除息的已实施税前现金派息 / 当前收盘价 ×100%。","当前价格下过去一年实际现金股息率。","历史派息不保证下一年继续。");
  define("初始100万元累计现金","固定初始投入 100 万元，模拟持股数后累计现金分红。","看不再投资时历史现金流累计贡献。","税费、滑点、现金再投资都可能改变实际收益。");
  define("再投资总回报|总回报（未再投）","[(当前持仓价值 + 未投资现金 + 已取现现金) / 初始投入−1]×100%；再投资情景按实际模拟复购。","总收益同时考虑本金价格与现金分红。","样本窗口与复购/税费假设会影响结果。");
  define("2025投入成本股息率","FY2025 自然年模拟收到的现金分红 / 初始买入成本 ×100%。","从初始投入成本角度看当年的现金回报。","不是用当前市值作分母的实时股息率。");
  define("连续有现金股息","向 FY2025 逆向计数连续有已实施现金派息的财年数。","衡量已观测股息连续性。","不预测未来支付意愿或能力。");
  define("年化波动率","复权日收益率标准差 × √252 ×100%。","观察股票总回报日度变动的历史幅度。","历史日收益分布不保证稳定。");
  define("日收益CVaR 95%","取历史日收益最差 5% 样本的平均损失或负收益（按模型口径）。","观察最差尾部样本的平均跌幅。","受样本区间与异常市场影响；非必然损失上限。");
  define("Sortino (Rf=0)","相对零无风险利率的年化超额收益 / 年化下行偏差。","衡量单位下行波动对应的历史收益。","负值、低样本数及零下行偏差时应谨慎解读。");
  define("日收益观测数","统计参与风险计算的有效历史日收益样本。","判断风险统计是否有足够历史覆盖。","样本数量多并不保证市场环境代表性。");
  define("A股研究池|已取得分红|已有收盘价|当前筛选","按证券池/已确认现金派息/行情覆盖/当前筛选条件逐项计数。","数据范围与样本覆盖指标。","计数不代表数据质量或未来收益。");

  // Asset cashflow summary and income strategies.
  define("跟踪资产","当前生成快照包含的注册资产行数。","研究覆盖资产数量。","不是可投资清单或流动性指标。");
  define("期权矩阵","具有期权矩阵的资产数量 × 两种策略（Covered Call / Cash-Secured Put）。","可比较的策略矩阵数量。","模拟可报价性不代表真实下单可成交。");
  define("平均现金收益率","对有最佳策略年化现金收益率的资产求简单算术平均。","衡量被跟踪资产当前模型收益报价的平均水平。","不是市值加权、风险调整或可实现组合收益。");
  define("最高策略收益率","取快照中最佳策略模型年化现金收益的最大值。","找到当前模型报价较高的收益机会。","收益率高通常伴随卖出期权风险，不能作最佳投资建议。");
  define("Current price (USD)","来自资产估值中的最新美元报价（本地币种资产按所用汇率换算）。","对应单位资产的最新观测估值。","行情日期/FX 时间必须一致检查。");
  define("Asset change (USD)|Asset value change","（当前同单位美元资产估值/起始美元资产估值−1）×100%。","衡量起始至当前的本金价格变化，不含派息。","不同估值频率与汇率可能影响可比性。");
  define("Measured cash yield","按实际已记录分配或策略模型现金收益年化的对应估值口径。","单独观察现金流贡献，不与本金上涨混为一谈。","历史派息和期权卖方权利金不具有同等确定性。");
  define("Total return CAGR (USD)","（区间末总回报指数/区间初指数）^(365/自然日数)−1。","将本金变化与现金流贡献折算成年复合收益。","强烈依赖样本窗；不是未来期望收益。");
  define("Annualized cash yield|Annualized cash yield / APR","历史分配按观察期年化；期权权利金按卖方可执行报价与 DTE 机械年化。","观察资产或策略给出的年化现金流尺度。","期权 APR 是机械年化，不含到期履约损失与实际可复投假设。");

  function resolve(label) {
    if(specs.has(label))return specs.get(label);
    if(/^Current price \(USD/.test(label))return specs.get("Current price (USD)");
    if(/^Asset change \([A-Z]+\)/.test(label))return specs.get("Asset change (USD)");
    return null;
  }
  function wrap(label, body, classes) {
    const spec=resolve(label);
    // Unregistered metrics still get a transparent, non-fabricated disclosure.
    const item=spec || {
      formula:"当前指标直接来自已验证的数据快照；推导公式尚未在指标词典登记。",
      meaning:"查看指标数值时需结合取样时间和数据来源。",
      boundary:"当前无可核验的专属计算说明，请勿作预测或交易决策。",source:"待补充",
    };
    const detail=(title,content)=>'<div class="metric-explain-row"><strong>'+esc(title)+
      '</strong><p>'+esc(content)+'</p></div>';
    return '<details class="'+esc(classes)+' metric-disclosure" data-metric="'+esc(label)+'">'+
      '<summary>'+body+'<span class="metric-expand-indicator" aria-hidden="true">公式与解读 <span>＋</span></span></summary>'+
      '<div class="metric-explain">'+
      detail("计算公式 / 口径",item.formula)+detail("它说明什么",item.meaning)+
      detail("限制与边界",item.boundary)+
      (item.source?detail("数据来源",item.source):"")+'</div></details>';
  }
  root.NassetMetricGuide=Object.freeze({wrap,resolve,definitions:specs});
})(typeof globalThis==="object" ? globalThis : window);

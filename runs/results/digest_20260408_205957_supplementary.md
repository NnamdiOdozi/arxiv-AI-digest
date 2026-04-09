# Supplementary Digest 20260408_205957

Recovered 9 relevant papers from previous parse failures.

## Recovered Papers

### 1. [Mack-Net model: Blending Mack's model with Recurrent Neural Networks](http://arxiv.org/abs/2205.07334v1) [9/10]
*Eduardo Ramos-Pérez, Pablo J. Alonso-González, José Javier Núñez-Velázquez — 2022-05-15*
> The paper introduces Mack-Net, a hybrid model blending Mack’s stochastic reserving method with Recurrent Neural Networks to improve reserve estimation accuracy and better quantify liability variability, aligning with regulatory and risk management needs.
**Key insight:** Blending Mack’s model with RNNs enhances both point estimates and uncertainty quantification in general insurance reserving, offering practical value for actuarial practice.

### 2. [SynthETIC: an individual insurance claim simulator with feature control](http://arxiv.org/abs/2008.05693v4) [9/10]
*Benjamin Avanzi, Gregory Clive Taylor, Melantha Wang et al. — 2020-08-13*
> The paper introduces SynthETIC, an open-source simulator for generating individual insurance claims with controllable features like settlement rates, inflation, and dependencies, enabling realistic testing of machine learning reserving models.
**Key insight:** SynthETIC fills a critical gap by providing actuarial researchers with customizable, realistic synthetic claim data to develop and validate ML reserving methods where real data is scarce.

### 3. [mCube: Multinomial Micro-level reserving Model](http://arxiv.org/abs/2212.00101v1) [9/10]
*Emmanuel Jordy Menvouta, Jolien Ponnet, Robin Van Oirbeek et al. — 2022-11-30*
> The paper introduces mCube, a multinomial micro-level reserving model that jointly models claim timing and payment processes for IBNR and RBNS claims, demonstrating strong performance on real bodily injury claims data with well-calibrated reserve estimates.
**Key insight:** mCube provides a unified, probabilistic micro-level reserving framework that accurately models both claim development timing and payment amounts, producing well-centered reserve distributions for real-world bodily injury claims.

### 4. [Ensemble distributional forecasting for insurance loss reserving](http://arxiv.org/abs/2206.08541v5) [9/10]
*Benjamin Avanzi, Yanfeng Li, Bernard Wong et al. — 2022-06-17*
> The paper introduces a systematic framework for ensembling stochastic loss reserving models, focusing on full distributional properties and tailoring to reserving-specific data features like accident and development periods. It demonstrates superior performance over traditional methods using a synthetic dataset and provides an R package for implementation.
**Key insight:** Ensembling stochastic reserving models by optimizing their full predictive distributions—rather than just point estimates—yields better reserve estimates and quantiles, especially under sparse or heterogeneous claim data.

### 5. [Continuous-time modeling and bootstrap for chain-ladder reserving](http://arxiv.org/abs/2406.03252v2) [8/10]
*Nicolas Baradel — 2024-06-05*
> The paper extends Mack’s chain-ladder model using a continuous-time stochastic differential equation and proposes a bootstrap method that naturally captures asymmetry and non-negativity in reserve distributions, validated via case study.
**Key insight:** A continuous-time SDE framework for chain-ladder reserving enables more realistic reserve distribution estimation without ad hoc assumptions, improving uncertainty quantification.

### 6. [Modeling and measuring incurred claims risk liabilities for a multi-line property and casualty insurer](http://arxiv.org/abs/2007.07068v1) [8/10]
*Carlos Andrés Araiza Iturria, Frédéric Godin, Mélina Mailhot — 2020-07-14*
> The paper presents a stochastic model for multi-line P&C insurers to measure incurred claims risk and capital requirements, using a double GLM with hierarchical copulas to capture loss ratio dynamics and diversification benefits under IFRS 17.
**Key insight:** The model enables joint simulation of loss triangles and quantifies portfolio-level risk and diversification benefits, offering practical value for reserving and capital allocation under modern accounting standards.

### 7. [Bridging the gap between pricing and reserving with an occurrence and development model for non-life insurance claims](http://arxiv.org/abs/2203.07145v2) [8/10]
*Jonas Crevecoeur, Katrien Antonio, Stijn Desmedt et al. — 2022-03-14*
> The paper proposes a granular occurrence and development model that unifies pricing and reserving by addressing right-censored claim data, improving upon traditional two-step methods that ignore uncertainty in best estimates, particularly beneficial for reinsurance with long delays and heavy-tailed claims.
**Key insight:** A unified occurrence-and-development model resolves inconsistencies between pricing and reserving by directly modeling censored claim data, preserving uncertainty and offering practical value for portfolios with long settlement delays.

### 8. [Generative Synthesis of Insurance Datasets](http://arxiv.org/abs/1912.02423v2) [7/10]
*Kevin Kuo — 2019-12-05*
> The paper introduces a CTGAN-based workflow to synthesize realistic insurance datasets for general and life insurance, evaluated on ML efficacy, variable distributions, and model stability, with an R interface to encourage adoption.
**Key insight:** Synthetic insurance datasets generated via CTGAN can support actuarial research by enabling reproducible experiments where real data is unavailable or restricted.

### 9. [A Structured Nonparametric Framework for Nonlinear Accelerated Failure Time Models (KAN-AFT)](http://arxiv.org/abs/2512.20305v2) [7/10]
*Mebin Jose, Jisha Francis, Sudheesh Kumar Kattumannil — 2025-12-23*
> The paper introduces KAN-AFT, a nonparametric extension of accelerated failure time models using Kolmogorov–Arnold representations to capture nonlinear covariate effects in survival analysis, with applications in clinical settings and support for censored data.
**Key insight:** KAN-AFT enables flexible, interpretable modeling of nonlinear survival time relationships under censoring, offering potential transferability to actuarial claim development and reserving where time-to-event and covariate interactions matter.

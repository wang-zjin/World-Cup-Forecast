import numpy as np
from scipy.optimize import minimize

mu = np.array([0.475, 0.176, 0.248, 0.700, 0.050, 0.015])

Sigma = np.array([
    [34.699375, 0.000000,  0.000000,  0.000000,   0.000000,  0.000000],
    [ 0.000000,23.313024,  0.000000,  0.000000,   0.000000,  0.000000],
    [ 0.000000, 0.000000, 38.378496, -2.121600,   0.000000,  0.000000],
    [ 0.000000, 0.000000, -2.121600,167.110000,   0.000000,  0.000000],
    [ 0.000000, 0.000000,  0.000000,  0.000000,  14.647500, -1.065750],
    [ 0.000000, 0.000000,  0.000000,  0.000000,  -1.065750,  6.328525],
])

def neg_reward_risk_ratio(w):
    expected_return = w @ mu
    variance = w @ Sigma @ w
    if variance <= 0:
        return 1e9
    return -expected_return / np.sqrt(variance)

constraints = [
    {"type": "eq", "fun": lambda w: np.sum(w) - 1},
]
bounds = [(0, 1)] * len(mu)

initial_points = [np.ones(len(mu)) / len(mu)]
for i in range(len(mu)):
    point = np.zeros(len(mu))
    point[i] = 1
    initial_points.append(point)

rng = np.random.default_rng(42)
for _ in range(100):
    initial_points.append(rng.dirichlet(np.ones(len(mu))))

best = None
for x0 in initial_points:
    result = minimize(
        neg_reward_risk_ratio,
        x0=x0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    if result.success and (best is None or result.fun < best.fun):
        best = result

w = best.x
expected_return = w @ mu
standard_deviation = np.sqrt(w @ Sigma @ w)
reward_risk_ratio = expected_return / standard_deviation

print("w =", np.round(w, 6))
print("期望收益率 =", round(expected_return, 6))
print("收益标准差 =", round(standard_deviation, 6))
print("收益风险比 =", round(reward_risk_ratio, 6))
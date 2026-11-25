import re
w = open("../wii_esc/wii_report.tex").read()
body = w[w.index(r"\section{Setup}"):w.index(r"\section*{Reproducibility}")]
# Rename sections of the Wii/Esc part and use labels instead of hard-coded numbers.
body = body.replace(r"\section{Setup}", r"\section{Budget-aware tuning: setup}\label{sec:setup}", 1)
body = body.replace(r"\section{Results}", r"\section{Wii: results}", 1)
body = body.replace(r"\subsection{Why skipping is safe: derived against real costs}", r"\subsection{Why skipping is safe: derived against real costs}\label{sec:gaps}", 1)
body = body.replace(r"\subsection{Assumptions of the cost model}", r"\subsection{Assumptions of the cost model}\label{sec:assump}", 1)
body = body.replace(r"\section{Esc: early stopping}", r"\section{Esc: early stopping}\label{sec:esc}", 1)
body = body.replace(r"\subsection{Storage-limited setting}", r"\subsection{Storage-limited setting}\label{sec:storage}", 1)
body = body.replace(r"\section{Comparison with the eight algorithms of the original study}", r"\section{Comparison with the eight algorithms}\label{sec:compare}", 1)
body = body.replace(r"\section{Real execution}", r"\section{Real execution}\label{sec:exec}", 1)
body = body.replace(r"except in Section~4 and Section~3.1, which use", r"except in Sections~\ref{sec:compare} and~\ref{sec:storage}, which use", 1)
body = body.replace(r"Section~4 uses its own runs", r"Section~\ref{sec:compare} uses its own runs", 1)
body = body.replace(r"(Sections 2.3 and 2.4)", r"(Sections~\ref{sec:gaps} and~\ref{sec:assump})", 1)
body = body.replace(r"(Section~4) uses three", r"(Section~\ref{sec:compare}) uses three", 1)
# Cut the old discussion/status sections; the final report has its own.
i = body.index(r"\section{Discussion and limitations}")
body = body[:i]
# Esc ablation sentence: keep the claim as not measured.
open("body_wii.tex", "w").write(body)
print("sections kept:", re.findall(r"\\section\{([^}]*)\}", body))
print("remaining manual refs:", re.findall(r"Sections?~?\s?\d[\d.]*", body))

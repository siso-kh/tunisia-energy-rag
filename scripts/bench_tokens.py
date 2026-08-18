"""Benchmark: token usage of realistic conversations vs the HISTORY_TOKEN_BUDGET."""
from src.rag.retrieve import HISTORY_TOKEN_BUDGET
from src.utils.token_manager import get_optimized_history, tokenizer


def msg_tokens(role: str, content: str) -> int:
    """Same token accounting as the utility: role:content + 4 structural buffer."""
    return len(tokenizer.encode(f"{role}: {content}")) + 4


def analyze(label: str, history) -> None:
    raw = sum(msg_tokens(m["role"], m["content"]) for m in history)
    budgeted = get_optimized_history(history, max_tokens=HISTORY_TOKEN_BUDGET)
    used = sum(msg_tokens(m["role"], m["content"]) for m in budgeted)
    dropped = len(history) - len(budgeted)
    headroom = HISTORY_TOKEN_BUDGET - used
    pct = 100.0 * used / HISTORY_TOKEN_BUDGET
    # also check the function's default budget (2000) for reference
    default_kept = len(get_optimized_history(history, max_tokens=2000))
    print(f"\n[{label}]")
    print(f"  raw tokens (no budget)      : {raw}")
    print(f"  budget {HISTORY_TOKEN_BUDGET}: {len(budgeted)}/{len(history)} msgs kept, {used} tokens used ({pct:.1f}% of budget)")
    print(f"  dropped messages            : {dropped}")
    print(f"  headroom left               : {headroom} tokens")
    print(f"  kept under default 2000     : {default_kept} msgs")


def build_normal_conv():
    return [
        {"role": "user", "content": "Bonjour, j'aimerais des informations sur la transition energetique en Tunisie."},
        {"role": "assistant", "content": "Bonjour ! La Tunisie vise 35% d'electricite renouvelable d'ici 2030, portee par l'ANME et la STEG."},
        {"role": "user", "content": "Quels sont les programmes d'aide pour installer des panneaux solaires chez soi ?"},
        {"role": "assistant", "content": "Le Fonds de Transition Energetique (FTE) accorde des primes. Le programme PROSOL couvre aussi les chauffe-eau solaires."},
        {"role": "user", "content": "Quelles conditions pour beneficier du regime d'autoconsommation ?"},
        {"role": "assistant", "content": "La loi n°2015-12 encadre l'autoconsommation : il faut un contrat avec la STEG et une installation conforme aux normes."},
        {"role": "user", "content": "Y a-t-il des aides pour les entreprises industrielles ?"},
        {"role": "assistant", "content": "Oui, l'audit energetique est obligatoire pour les grandes industries, et des subventions couvrent une partie des investissements."},
        {"role": "user", "content": "Merci ! Et pour l'efficacite energetique dans les batiments neufs ?"},
        {"role": "assistant", "content": "La reglementation thermique RTBNT impose des exigences d'isolation et de performance pour les batiments neufs."},
    ]


def build_verbose_conv():
    """Longer, detailed messages (~400-600 chars each) - heavy but realistic."""
    return [
        {"role": "user", "content": "Bonjour, j'aimerais des informations completes sur la transition energetique en Tunisie : objectifs, acteurs principaux, et financements disponibles. Pouvez-vous detailler ?"},
        {"role": "assistant", "content": "Bonjour ! La Tunisie vise 35% d'electricite renouvelable d'ici 2030, principalement solaire photovoltaique et eolien. Les acteurs cles sont l'ANME (politique et maitrise de l'energie), la STEG (infrastructure et raccordement) et le Ministere de l'Industrie, des Mines et de l'Energie. Le Fonds de Transition Energetique finance les projets."},
        {"role": "user", "content": "Quels sont precisement les programmes d'aide pour installer des panneaux solaires en autoconsommation chez un particulier, et quelles primes sont versees ?"},
        {"role": "assistant", "content": "Le regime d'autoconsommation est encadre par la loi n°2015-12. Le Fonds de Transition Energetique (FTE) accorde des primes a l'investissement pour les installations photovoltaiques. Le programme PROSOL, gere par la STEG, couvre egalement les chauffe-eau solaires avec des credits bonifies. Les demandes se font aupres de l'ANME."},
        {"role": "user", "content": "Quelles sont les conditions reglementaires pour beneficier du regime d'autoconsommation, notamment en termes de puissance et de raccordement au reseau ?"},
        {"role": "assistant", "content": "La loi n°2015-12 et ses decrets d'application encadrent l'autoconsommation : le producteur doit signer un contrat de raccordement avec la STEG, respecter les normes techniques (compteur bidirectionnel), et les installations au-dela de certains seuils necessitent une autorisation. L'ANME fournit les guides pratiques."},
        {"role": "user", "content": "Y a-t-il des aides similaires pour les entreprises industrielles et le secteur tertiaire, et l'audit energetique est-il obligatoire ?"},
        {"role": "assistant", "content": "Oui. L'audit energetique est obligatoire pour les etablissements industriels et tertiaires depassant certains seuils de consommation. Le FTE subventionne une partie du cout de l'audit et des travaux d'efficacite energetique. Les entreprises peuvent aussi beneficier de conventions avec l'ANME pour le suivi de leurs objectifs."},
        {"role": "user", "content": "Et pour les batiments neufs, quelles sont les exigences de la reglementation thermique et comment sont-elles controlees ?"},
        {"role": "assistant", "content": "La reglementation thermique des batiments neufs (RTBNT) impose des exigences d'isolation, de performance des equipements et d'apport solaire passif. Le controle est assure lors du permis de construire et de la reception des travaux. Des attestations sont exigees, et l'ANME assure la sensibilisation des professionnels du batiment."},
    ]


if __name__ == "__main__":
    print(f"History token budget: {HISTORY_TOKEN_BUDGET}")
    analyze("NORMAL 10-message conversation", build_normal_conv())
    analyze("VERBOSE 6-message conversation (long answers)", build_verbose_conv())

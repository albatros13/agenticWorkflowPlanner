"""Gold-standard few-shot examples for FLINT semantic-role labelling.

These are the hand-annotated examples from FlintFiller-SRL's best-performing
"function-call + few-shot combo" variant (``label_text_funct_few_combi.py`:
``message_srl_en`` / ``message_srl_nl``). They are the main reason the method works
well, so they are kept verbatim — only re-shaped from OpenAI ``example_user`` /
``example_assistant`` message pairs into provider-neutral ``(sentence, roles)``
records that the labeller renders into whatever prompt format a provider needs.

Each record: ``{"sentence": str, "roles": {action, actor, object, recipient, other}}``.
"""
from __future__ import annotations

# English (CRR / banking-regulation sentences).
FEW_SHOT_EN: list[dict] = [
    {
        "sentence": "For this purpose , institutions shall calculate the exposure value of the items listed in Article 166(8) to (10) by using a conversion factor or percentage of 100 % rather than the conversion factors or percentages indicated in those paragraphs . ",
        "roles": {
            "action": ["shall", "calculate"],
            "actor": ["institutions"],
            "object": ["the", "exposure", "value", "of", "the", "items", "listed", "in", "Article", "166", "(", "8", ")", "to", "(", "10", ")"],
            "recipient": [],
            "other": ["For", "this", "purpose", ",", "by", "using", "a", "conversion", "factor", "or", "percentage", "of", "100", "%", "rather", "than", "the", "conversion", "factors", "or", "percentages", "indicated", "in", "those", "paragraphs", "."],
        },
    },
    {
        "sentence": "Institutions shall calculate the nth lowest amount as specified in points (a) and (b) of Article 240 . ",
        "roles": {
            "action": ["shall", "calculate"],
            "actor": ["Institutions"],
            "object": ["the", "nth", "lowest", "amount", "as", "specified", "in", "points", "(", "a", ")", "and", "(", "b", ")", "of", "Article", "240"],
            "recipient": [],
            "other": ["."],
        },
    },
    {
        "sentence": "In order to calculate interest rate risk position , institutions shall apply the following provisions . ",
        "roles": {
            "action": ["shall", "apply"],
            "actor": ["institutions"],
            "object": ["the", "following", "provisions"],
            "recipient": [],
            "other": ["In", "order", "to", "calculate", "interest", "rate", "risk", "position", ",", "."],
        },
    },
    {
        "sentence": "An institution may calibrate its EPE model using either historic market data or market implied data to establish parameters of the underlying stochastic processes , such as drift , volatility and correlation . ",
        "roles": {
            "action": ["may", "calibrate"],
            "actor": ["An", "institution"],
            "object": ["its", "EPE", "model"],
            "recipient": [],
            "other": ["using", "either", "historic", "market", "data", "or", "market", "implied", "data", "to", "establish", "parameters", "of", "the", "underlying", "stochastic", "processes", ",", "such", "as", "drift", ",", "volatility", "and", "correlation", "."],
        },
    },
    {
        "sentence": "Over time , an institution shall validate and reassess the process and the outcomes through comparison to actual internal loss experience and relevant external data . ",
        "roles": {
            "action": ["shall", "validate", "and", "reassess"],
            "actor": ["an", "institution"],
            "object": ["the", "process", "and", "the", "outcomes"],
            "recipient": [],
            "other": ["Over", "time", ",", "through", "comparison", "to", "actual", "internal", "loss", "experience", "and", "relevant", "external", "data", "."],
        },
    },
]

# Dutch (Nederlandse wetgeving).
FEW_SHOT_NL: list[dict] = [
    {
        "sentence": "De bevoegdheden van de procureur - generaal kunnen ,  tenzij de aard van de bevoegdheden zich daartegen verzet ,  mede worden uitgeoefend door de plaatsvervangend procureur - generaal en door advocaten - generaal . ",
        "roles": {
            "action": ["kunnen", "worden"],
            "actor": ["door", "de", "plaatsvervangend", "procureur", "-", "generaal", "en", "door", "advocaten", "-", "generaal"],
            "object": ["De", "bevoegdheden", "van", "de", "procureur", "-", "generaal"],
            "recipient": [],
            "other": [",", "tenzij", "de", "aard", "van", "de", "bevoegdheden", "zich", "daartegen", "verzet", ",", "mede", "uitgeoefend", "."],
        },
    },
    {
        "sentence": "De rechtsvordering tot wijziging in de gevallen in  vermeld vervalt ,  voor zooveel zij niet steunt op feiten ,  die na de vaststelling van de betrokken bepalingen van den legger hebben plaats gevonden ,  indien zij niet is ingesteld binnen n jaar ,  nadat de bepaling van den legger ,  tegen welke men opkomt ,  bij eindbeslissing is vastgesteld of gehandhaafd . artikel 43",
        "roles": {
            "action": ["vervalt"],
            "actor": [],
            "object": ["De", "rechtsvordering", "tot", "wijziging", "in", "de", "gevallen", "in", "vermeld"],
            "recipient": [],
            "other": [",", "voor", "zooveel", "zij", "niet", "steunt", "op", "feiten", ",", "die", "na", "de", "vaststelling", "van", "de", "betrokken", "bepalingen", "van", "den", "legger", "hebben", "plaats", "gevonden", ",", "indien", "zij", "niet", "is", "ingesteld", "binnen", "n", "jaar", ",", "nadat", "de", "bepaling", "van", "den", "legger", ",", "tegen", "welke", "men", "opkomt", ",", "bij", "eindbeslissing", "is", "vastgesteld", "of", "gehandhaafd", ".", "artikel", "43"],
        },
    },
    {
        "sentence": "De Raad regelt de samenstelling ,  bevoegdheid en werkwijze van deze commissies en benoemt de leden . ",
        "roles": {
            "action": ["regelt", "benoemt"],
            "actor": ["De", "Raad"],
            "object": ["de", "samenstelling", "bevoegdheid", "en", "werkwijze", "van", "deze", "commissies", "de", "leden"],
            "recipient": [],
            "other": [",", "en", "."],
        },
    },
    {
        "sentence": "Na het overlijden van degene ,  aan wie ouderdomspensioen is toegekend ,  wordt met ingang van de dag na het overlijden ,  ouderdomspensioen in de vorm van een overlijdensuitkering uitbetaald : ",
        "roles": {
            "action": ["wordt", "uitbetaald"],
            "actor": [],
            "object": ["ouderdomspensioen", "in", "de", "vorm", "van", "een", "overlijdensuitkering"],
            "recipient": [],
            "other": ["Na", "het", "overlijden", "van", "degene", ",", "aan", "wie", "ouderdomspensioen", "is", "toegekend", ",", "met", "ingang", "van", "de", "dag", "na", "het", "overlijden", ",", ":"],
        },
    },
    {
        "sentence": "Een inhoudingsplichtige die voor de heffing van de vennootschapsbelasting is aangemerkt als beleggingsinstelling als bedoeld in  mag op de ingevolge  ,  op aangifte af te dragen belasting een vermindering toepassen wegens ten laste van hem ingehouden dividendbelasting en buitenlandse bronheffing . artikel 7 ,  vierde lidartikel 28 van de Wet op de vennootschapsbelasting 1969",
        "roles": {
            "action": ["mag", "toepassen"],
            "actor": ["Een", "inhoudingsplichtige", "die", "voor", "de", "heffing", "van", "de", "vennootschapsbelasting", "is", "aangemerkt", "als", "beleggingsinstelling", "als", "bedoeld", "in"],
            "object": ["een", "vermindering"],
            "recipient": [],
            "other": ["op", "de", "ingevolge", ",", "op", "aangifte", "af", "te", "dragen", "belasting", "wegens", "ten", "laste", "van", "hem", "ingehouden", "dividendbelasting", "en", "buitenlandse", "bronheffing", ".", "artikel", "7", ",", "vierde", "lidartikel", "28", "van", "de", "Wet", "op", "de", "vennootschapsbelasting", "1969"],
        },
    },
]

EXAMPLES_BY_LANGUAGE: dict[str, list[dict]] = {
    "en": FEW_SHOT_EN,
    "nl": FEW_SHOT_NL,
}

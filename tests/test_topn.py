from datetime import date

from job_radar import topn
from job_radar.sheet import TOP_TAB, tracker_tab, write_top

TODAY = date(2026, 10, 21)


def row(**kw):
    base = {"uid": "x", "Company": "acme", "Role": "Software Engineer Intern", "Applied": "FALSE",
            "Fit": "80", "Resume": "SWE", "Term": "Summer 2027", "Why": "", "Link": "https://a.b/1",
            "Location": "Toronto, ON", "Posted": "", "Status": "New", "Deadline": "",
            "Priority": ""}
    base.update(kw)
    return base


def s(**kw):
    return topn.score(row(**kw), TODAY)[0]


def test_ml_beats_plain_swe_and_low_level_sinks():
    ml = s(Role="Machine Learning Engineer Intern")
    swe = s()
    low = s(Role="ML Compiler Intern")
    assert ml > swe > low


def test_research_ranks_lower_but_data_scientist_does_not():
    assert s(Role="Research Intern, AI") < s(Role="AI Engineer Intern")
    assert s(Role="Data Scientist Intern") > s()


def test_long_term_coop_and_canada_boosts():
    assert s(Term="Summer 2027, 16 months") > s()
    assert s(Term="12-month co-op starting Sep 2027") > s()
    assert s(Location="Austin, TX") < s()
    assert s(Location="Remote in USA") < s(Location="Remote, Canada")


def test_flags_and_deadlines_jump_up():
    assert s(Priority="must") > s(Priority="high") > s()
    assert s(Deadline="2026-10-25") > s()


def test_eligible_drops_done_and_too_early_terms():
    assert topn.eligible(row())
    assert not topn.eligible(row(Applied="TRUE"))
    assert not topn.eligible(row(Status="Closed"))
    assert not topn.eligible(row(Term="Fall 2026"))
    # since Nov 2026, winter and early-2027 starts are fine
    assert topn.eligible(row(Term="Winter 2027"))
    assert topn.eligible(row(Term="Jan 2027, 12 months"))
    assert topn.eligible(row(Term="Winter Co-op"))
    assert topn.eligible(row(Term="not stated"))


def test_top_dedupes_same_role_and_unpacks_feed_names():
    rows = [row(Company="simplify", Role="Pebl: SWE Intern", Location="Toronto"),
            row(Company="simplify", Role="Pebl: SWE Intern", Location="Vancouver"),
            row(Company="lyft", Role="ML Intern", Fit="90")]
    ranked = topn.top(rows, TODAY)
    assert [topn.display(r) for _, _, r in ranked] == [("lyft", "ML Intern"), ("Pebl", "SWE Intern")]


def test_top_lists_every_worthwhile_role_with_no_cap():
    rows = [row(uid=str(i), Role=f"Intern {i}") for i in range(50)]
    assert len(topn.top(rows, TODAY)) == 50
    assert len(topn.top(rows, TODAY, n=5)) == 5


def test_worth_applying_drops_what_he_cannot_or_will_not_do():
    assert topn.worth_applying(row())
    assert topn.worth_applying(row(Role="Product Manager Intern"))
    assert topn.worth_applying(row(Role="Data Analyst", Term="Summer 2027 co-op"))
    assert topn.why_not(row(Applied="TRUE")) == "not open"
    assert topn.why_not(row(Status="Closed")) == "not open"
    assert topn.why_not(row(Term="Fall 2026")) == "term already started"
    assert topn.why_not(row(Flags="low-level")) == "low-level"
    assert topn.why_not(row(Flags="senior")) == "senior"
    assert topn.why_not(row(Flags="grad degree")) == "grad degree"
    assert topn.why_not(row(Flags="not a student role")) == "not a student role"
    assert topn.why_not(row(Role="Senior Software Engineer")) == "senior or grad-level title"
    assert topn.why_not(row(Role="PhD Research Intern")) == "senior or grad-level title"
    assert topn.why_not(row(Role="Software Engineer, New Grad")) == "senior or grad-level title"
    assert topn.why_not(row(Role="Compiler Engineer Intern")) == "low-level"
    assert topn.why_not(row(Role="Data Scientist")) == "not an internship or co-op"
    assert topn.why_not(row(Fit="55")) == "fit under 60"
    # roles from the aggregator feeds are judged on the real title
    assert topn.worth_applying(row(Company="simplify", Role="Pebl: ML Intern"))


def test_table_links():
    ranked = topn.top([row(Company="lyft", Role='ML "Intern"')], TODAY)
    plain = topn.table(ranked)
    assert plain[0] == topn.TOP_HEADERS
    assert plain[1][2] == 80
    assert plain[1][11] == 'prep lyft: ML "Intern"'
    assert plain[1][10] == '=HYPERLINK("https://a.b/1", "Apply")'
    linked = topn.table(ranked, "https://example.com/t")
    assert linked[1][11] == '=HYPERLINK("https://example.com/t", "prep lyft: ML ""Intern""")'


class FakeTab:
    def __init__(self):
        self.values = None
        self.cleared = False
        self.rows = None

    def resize(self, rows, cols):
        self.rows = rows

    def clear(self):
        self.cleared = True

    def update(self, range_name, values, value_input_option=None):
        self.values = values


class FakeBook:
    def __init__(self, has_tab):
        self.tab = FakeTab() if has_tab else None
        self.added = False

    def worksheet(self, title):
        if self.tab is None:
            raise KeyError(title)
        return self.tab

    def add_worksheet(self, title, rows, cols, index=None):
        assert index is None          # must never become the first tab
        self.added = True
        self.tab = FakeTab()
        return self.tab


class FakeWS:
    def __init__(self, records, book, title="Sheet1"):
        self._records = records
        self.spreadsheet = book
        self.title = title

    def get_all_records(self):
        return self._records


def test_write_top_creates_tab_and_leaves_tracker_alone():
    records = [row(Company="lyft", Role="ML Intern"), row(Status="Closed")]
    snapshot = [dict(r) for r in records]
    book = FakeBook(has_tab=False)
    n = write_top(FakeWS(records, book), TODAY)
    assert n == 1 and book.added and book.tab.cleared
    assert book.tab.values[1][3:5] == ["lyft", "ML Intern"]
    assert records == snapshot


def test_write_top_reuses_existing_tab():
    book = FakeBook(has_tab=True)
    write_top(FakeWS([row()], book), TODAY)
    assert not book.added and len(book.tab.values) == 2
    assert book.tab.rows == 2 + 5          # grid sized to the list, not a fixed 30


def test_old_top_30_tab_is_never_the_tracker():
    assert tracker_tab(Book(["Top 30", "Sheet1"])).title == "Sheet1"


def test_write_top_refuses_to_rank_itself():
    book = FakeBook(has_tab=True)
    assert write_top(FakeWS([row()], book, title=TOP_TAB), TODAY) == 0
    assert book.tab.values is None


class Book:
    def __init__(self, titles):
        self._tabs = [FakeWS([], None, title=t) for t in titles]
        self.sheet1 = self._tabs[0]

    def worksheets(self):
        return self._tabs


def test_tracker_tab_skips_top_tab_wherever_it_sits():
    assert tracker_tab(Book([TOP_TAB, "Sheet1"])).title == "Sheet1"
    assert tracker_tab(Book(["Sheet1", TOP_TAB])).title == "Sheet1"
    assert tracker_tab(Book(["Jobs"])).title == "Jobs"


def test_yc_companies_rank_up():
    yc = frozenset({"pebl"})
    plain = topn.score(row(uid="ashby:acme:1"), TODAY, yc)[0]
    board = topn.score(row(uid="ashby:pebl:1", Company="pebl"), TODAY, yc)
    assert board[0] == plain + topn.YC_BOOST and "YC" in board[1]
    # a YC company reached through an aggregator feed is matched by its name
    feed = topn.score(row(uid="simplify:simplify:9", Company="simplify", Role="Pebl: SWE Intern"),
                      TODAY, yc)
    assert "YC" in feed[1]
    # no YC set (old callers) -> unchanged
    assert topn.score(row(uid="ashby:pebl:1"), TODAY)[0] == plain


def test_data_science_and_big_names_rank_up():
    assert s(Role="Data Scientist Intern") > s(Role="Machine Learning Engineer Intern")
    assert s(Company="google", Role="Data Scientist Intern") > s(Role="Data Scientist Intern")
    assert s(Company="simplify", Role="Google: SWE Intern") > s()
    assert s(Company="googleplex-clone") == s()

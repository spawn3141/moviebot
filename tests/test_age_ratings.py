import unittest

from moviebot.age_ratings import age_rating


def movie(**countries) -> dict:
    return {"release_dates": {"results": [
        {"iso_3166_1": c, "release_dates": [{"certification": cert, "type": 3} for cert in certs]}
        for c, certs in countries.items()]}}


def tv(**countries) -> dict:
    return {"content_ratings": {"results": [
        {"iso_3166_1": c, "rating": rating} for c, rating in countries.items()]}}


class AgeRatingTest(unittest.TestCase):
    def test_fsk_wins_over_us(self):
        self.assertEqual(age_rating(movie(DE=["12"], US=["R"]), "movie"), (12, "fsk", "12"))

    def test_strictest_german_entry(self):
        self.assertEqual(age_rating(movie(DE=["", "12", "16"]), "movie"), (16, "fsk", "16"))

    def test_us_movie_converted(self):
        self.assertEqual(age_rating(movie(US=["PG-13"]), "movie"), (12, "us", "PG-13"))
        self.assertEqual(age_rating(movie(US=["G"]), "movie"), (0, "us", "G"))
        self.assertEqual(age_rating(movie(DE=[""], US=["R"]), "movie"), (16, "us", "R"))

    def test_series(self):
        self.assertEqual(age_rating(tv(DE="6", US="TV-14"), "tv"), (6, "fsk", "6"))
        self.assertEqual(age_rating(tv(US="TV-Y7"), "tv"), (6, "us", "TV-Y7"))
        self.assertEqual(age_rating(tv(US="TV-MA"), "tv"), (16, "us", "TV-MA"))

    def test_unknown(self):
        self.assertIsNone(age_rating({}, "movie"))
        self.assertIsNone(age_rating(movie(GB=["15"], US=["Unrated"]), "movie"))
        self.assertIsNone(age_rating(tv(DE=""), "tv"))


if __name__ == "__main__":
    unittest.main()

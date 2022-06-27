from typing import Tuple, Union, List
import math
import numpy as np
from trueskill import TrueSkill, Rating, rate_1vs1


class EloCalculator(object):
    score = {
        1: 1.0,  # win
        0: 0.5,  # draw
        -1: 0.0,  # lose
    }

    @classmethod
    def get_new_rating(cls,
                       rating_a: int,
                       rating_b: int,
                       result: int,
                       k_factor: int = 32,
                       beta: int = 200) -> Tuple[int, int]:
        assert result in [1, 0, -1]
        expect_a = 1. / (1. + math.pow(10, (rating_b - rating_a) / (2. * beta)))
        expect_b = 1. / (1. + math.pow(10, (rating_a - rating_b) / (2. * beta)))
        new_rating_a = rating_a + k_factor * (EloCalculator.score[result] - expect_a)
        new_rating_b = rating_b + k_factor * (1 - EloCalculator.score[result] - expect_b)
        return round(new_rating_a), round(new_rating_b)

    @classmethod
    def get_new_rating_array(
            cls,
            rating: np.ndarray,
            result: np.ndarray,
            game_count: np.ndarray,
            k_factor: int = 32,
            beta: int = 200
    ) -> np.ndarray:
        """
        Shapes:
            rating: :math:`(N, )`, N is the number of player
            result: :math:`(N, N)`
            game_count: :math:`(N, N)`
        """
        rating_diff = np.expand_dims(rating, 0) - np.expand_dims(rating, 1)
        expect = 1. / (1. + np.power(10, rating_diff / (2. * beta))) * game_count
        delta = ((result + 1.) / 2 - expect) * (game_count > 0)
        delta = delta.sum(axis=1)
        return np.round(rating + k_factor * delta).astype(np.int64)


class PlayerRating(Rating):

    def __init__(self, mu: float = None, sigma: float = None, elo_init: int = None) -> None:
        super(PlayerRating, self).__init__(mu, sigma)
        self.elo = elo_init

    def __repr__(self) -> str:
        c = type(self)
        args = ('.'.join([c.__module__, c.__name__]), self.mu, self.sigma, self.exposure, self.elo)
        return '%s(mu=%.3f, sigma=%.3f, exposure=%.3f, elo=%d)' % args


class LeagueMetricEnv(TrueSkill):
    """
    Overview:
        TrueSkill rating system among game players, for more details pleas refer to ``https://trueskill.org/``
    """

    def __init__(self, *args, elo_init: int = 1200, **kwargs) -> None:
        super(LeagueMetricEnv, self).__init__(*args, **kwargs)
        self.elo_init = elo_init

    def create_rating(self, mu: float = None, sigma: float = None, elo_init: int = None) -> PlayerRating:
        if mu is None:
            mu = self.mu
        if sigma is None:
            sigma = self.sigma
        if elo_init is None:
            elo_init = self.elo_init
        return PlayerRating(mu, sigma, elo_init)

    @staticmethod
    def _rate_1vs1(t1, t2, **kwargs):
        t1_elo, t2_elo = t1.elo, t2.elo
        t1, t2 = rate_1vs1(t1, t2, **kwargs)
        if 'drawn' in kwargs:
            result = 0
        else:
            result = 1
        t1_elo, t2_elo = EloCalculator.get_new_rating(t1_elo, t2_elo, result)
        t1 = PlayerRating(t1.mu, t1.sigma, t1_elo)
        t2 = PlayerRating(t2.mu, t2.sigma, t2_elo)
        return t1, t2

    def rate_1vs1(self,
                  team1: PlayerRating,
                  team2: PlayerRating,
                  result: List[str] = None,
                  **kwargs) -> Tuple[PlayerRating, PlayerRating]:
        if result is None:
            return self._rate_1vs1(team1, team2, **kwargs)
        else:
            for r in result:
                if r == 'wins':
                    team1, team2 = self._rate_1vs1(team1, team2)
                elif r == 'draws':
                    team1, team2 = self._rate_1vs1(team1, team2, drawn=True)
                elif r == 'losses':
                    team2, team1 = self._rate_1vs1(team2, team1)
                else:
                    raise RuntimeError("invalid result: {}".format(r))
        return team1, team2

    def rate_1vsC(self, team1: PlayerRating, team2: PlayerRating, result: List[str]) -> PlayerRating:
        for r in result:
            if r == 'wins':
                team1, _ = self._rate_1vs1(team1, team2)
            elif r == 'draws':
                team1, _ = self._rate_1vs1(team1, team2, drawn=True)
            elif r == 'losses':
                _, team1 = self._rate_1vs1(team2, team1)
            else:
                raise RuntimeError("invalid result: {}".format(r))
        return team1


get_elo = EloCalculator.get_new_rating
get_elo_array = EloCalculator.get_new_rating_array


if __name__ == '__main__':
    env = LeagueMetricEnv()
    p1 = env.create_rating()
    p2 = env.create_rating()
    print(id(p1))
    print(p1)
    print(id(p2))
    print(p2)
    print('############################')
    p1, p2 = env.rate_1vs1(p1, p2, result=['wins', 'wins'])
    print(id(p1))
    print(p1)
    print(id(p2))
    print(p2)
    print('############################')
    p1, p2 = env.rate_1vs1(p1, p2, result=['wins'])
    print(id(p1))
    print(p1)
    print(id(p2))
    print(p2)
    print('############################')

from ding.league import LeagueMetricEnv
def get_elo(demos, reward_model):
    ts_env = LeagueMetricEnv()
    world_size = len(demos)
    players = [{'rating': ts_env.create_rating(), 'demo': demos[i]} for i in range(world_size)]
    iter_num = 10

    def play_n(demo1, demo2, n=1):
        rand_length = np.random.randint(50, 100, size=n)
        min_len = min(len(demo1), len(demo2))
        res = []
        for _ in range(n):
            rand_length[n] = min(rand_length[n], min_len)
            t1_start = np.random.randint(min_len - rand_length[n] + 1)
            t2_start = np.random.randint(min_len - rand_length[n] + 1)
            t1 = demo1[t1_start:t1_start + rand_length[n]:2]
            t2 = demo2[t2_start:t2_start + rand_length[n]:2]

            r1 = reward_model.predict_traj_return(t1)
            r2 = reward_model.predict_traj_return(t2)

            if r1 > r2:
                res.append('wins')
            elif r1 == r2:
                res.append('draws')
            else:
                res.append('losses')
        return res

    for _ in range(iter_num):
        for i in range(world_size):
            for j in range(i + 1, world_size):
                play_res = play_n(players[i]['demo'], players[j]['demo'], n=3)
                players[i]['rating'], players[j]['rating'] = \
                    ts_env.rate_1vs1(players[i]['rating'], players[j]['rating'], result=play_res)

    return players


def add_elo(self, obs, rew):
    tmp = sorted(zip(rew, obs), key=lambda pair: pair[0][0])
    demonstrations = [x for _, x in tmp]
    with torch.no_grad():
        pls = get_elo(demonstrations, reward_model=self.reward_model)
        pred_elos = [pls[i]['rating'].elo for i in range(len(pls))]
    for ii in range(len(pred_elos)):
        print("real:" + str(tmp[ii][0]) + " pred: " + str(pred_elos[ii]))
        self.tb_logger.add_scalar('elo', pred_elos[ii], tmp[ii][0][0])

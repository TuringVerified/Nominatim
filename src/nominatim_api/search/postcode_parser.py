# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Nominatim. (https://nominatim.org)
#
# Copyright (C) 2025 by the Nominatim developer community.
# For a full list of authors see the git log.
"""
Handling of arbitrary postcode tokens in tokenized query string.
"""
from typing import Tuple, Set, Dict, List
import re
from collections import defaultdict

import yaml

from ..config import Configuration
from . import query as qmod


class PostcodeParser:
    """ Pattern-based parser for postcodes in tokenized queries.

        The postcode patterns are read from the country configuration.
        The parser does currently not return country restrictions.
    """

    def __init__(self, config: Configuration) -> None:
        # skip over includes here to avoid loading the complete country name data
        yaml.add_constructor('!include', lambda loader, node: [],
                             Loader=yaml.SafeLoader)
        cdata = yaml.safe_load(config.find_config_file('country_settings.yaml')
                                     .read_text(encoding='utf-8'))

        unique_patterns: Dict[str, Dict[str, List[str]]] = {}
        for cc, data in cdata.items():
            if data.get('postcode'):
                pat = data['postcode']['pattern'].replace('d', '[0-9]').replace('l', '[a-z]')
                out = data['postcode'].get('output')
                if pat not in unique_patterns:
                    unique_patterns[pat] = defaultdict(list)
                unique_patterns[pat][out].append(cc)

        self.global_pattern = re.compile(
                '(?:(?P<cc>[a-z][a-z])(?P<space>[ -]?))?(?P<pc>(?:(?:'
                + ')|(?:'.join(unique_patterns) + '))[:, >].*)')

        self.local_patterns = [(re.compile(f"{pat}[:, >]"), list(info.items()))
                               for pat, info in unique_patterns.items()]

    def parse(self, query: qmod.QueryStruct) -> Set[Tuple[int, int, str]]:
        """ Parse postcodes in the given list of query tokens taking into
            account the list of breaks from the nodes.

            The result is a sequence of tuples with
            [start node id, end node id, postcode token]
        """
        nodes = query.nodes
        outcodes: Set[Tuple[int, int, str]] = set()

        for i in range(query.num_token_slots()):
            if nodes[i].btype in '<,: ' and nodes[i + 1].btype != '`' \
                    and (i == 0 or nodes[i - 1].ptype != qmod.PHRASE_POSTCODE):
                if nodes[i].ptype == qmod.PHRASE_ANY:
                    word = nodes[i + 1].term_normalized + nodes[i + 1].btype
                    if word[-1] in ' -' and nodes[i + 2].btype != '`' \
                            and nodes[i + 1].ptype == qmod.PHRASE_ANY:
                        word += nodes[i + 2].term_normalized + nodes[i + 2].btype
                        if word[-1] in ' -' and nodes[i + 3].btype != '`' \
                                and nodes[i + 2].ptype == qmod.PHRASE_ANY:
                            word += nodes[i + 3].term_normalized + nodes[i + 3].btype

                    self._match_word(word, i, False, outcodes)
                elif nodes[i].ptype == qmod.PHRASE_POSTCODE:
                    word = nodes[i + 1].term_normalized + nodes[i + 1].btype
                    for j in range(i + 1, query.num_token_slots()):
                        if nodes[j].ptype != qmod.PHRASE_POSTCODE:
                            break
                        word += nodes[j + 1].term_normalized + nodes[j + 1].btype

                    self._match_word(word, i, True, outcodes)

        return outcodes

    def _match_word(self, word: str, pos: int, fullmatch: bool,
                    outcodes: Set[Tuple[int, int, str]]) -> None:
        # Use global pattern to check for presence of any postcode.
        m = self.global_pattern.fullmatch(word)
        if m:
            # If there was a match, check against each pattern separately
            # because multiple patterns might be machting at the end.
            cc = m.group('cc')
            pc_word = m.group('pc')
            cc_spaces = len(m.group('space') or '')
            for pattern, info in self.local_patterns:
                lm = pattern.fullmatch(pc_word) if fullmatch else pattern.match(pc_word)
                if lm:
                    trange = (pos, pos + cc_spaces + sum(c in ' ,-:>' for c in lm.group(0)))
                    for out, out_ccs in info:
                        if cc is None or cc in out_ccs:
                            if out:
                                outcodes.add((*trange, lm.expand(out).upper()))
                            else:
                                outcodes.add((*trange, lm.group(0)[:-1].upper()))

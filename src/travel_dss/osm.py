import gzip
import heapq
import json
import math
import xml.etree.ElementTree as ET

from .data import ROOT, write_json


def distance_meters(first, second):
    lat1, lon1, lat2, lon2 = map(math.radians, (*first, *second))
    value = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(value)))


def shortest_distances(graph, source):
    distances = {source: 0.0}
    queue = [(0.0, source)]
    while queue:
        value, node = heapq.heappop(queue)
        if value != distances[node]:
            continue
        for neighbor, length in graph[node]:
            candidate = value + length
            if candidate < distances.get(neighbor, float('inf')):
                distances[neighbor] = candidate
                heapq.heappush(queue, (candidate, neighbor))
    return distances


def prepare_city(city):
    source = ROOT / f'data/raw/osm/{city["id"]}.osm.gz'
    tree = ET.fromstring(gzip.decompress(source.read_bytes()))
    metadata = json.loads(source.with_suffix('.metadata.json').read_text())
    nodes = {e.attrib['id']: (float(e.attrib['lat']), float(e.attrib['lon'])) for e in tree.findall('node')}
    graph, candidates, seen = {}, [], set()
    for element in tree:
        tags = {tag.attrib['k']: tag.attrib['v'] for tag in element.findall('tag')}
        refs = [nd.attrib['ref'] for nd in element.findall('nd') if nd.attrib['ref'] in nodes]
        highway = tags.get('highway')
        if (highway and refs and tags.get('foot') not in ('no', 'private')
                and (tags.get('access') not in ('no', 'private') or tags.get('foot') in ('yes','designated','permissive'))
                and highway not in ('motorway','motorway_link','construction','proposed')
                and not (highway in ('trunk','trunk_link') and tags.get('foot') not in ('yes','designated'))):
            for left, right in zip(refs, refs[1:]):
                length = distance_meters(nodes[left], nodes[right])
                graph.setdefault(left, []).append((right, length))
                graph.setdefault(right, []).append((left, length))
        name = tags.get('name:ru', tags.get('name'))
        category = tags.get('tourism')
        park = tags.get('leisure') == 'park'
        if tags.get('access') in ('no', 'private') or name in ('Проход запрещён', 'IT покрути'):
            continue
        if not name or name.casefold() in seen or not (category in ('museum','gallery','attraction') or park):
            continue
        if element.tag == 'node':
            coordinates = nodes[element.attrib['id']]
        elif refs:
            coordinates = tuple(sum(nodes[r][axis] for r in refs)/len(refs) for axis in (0, 1))
        else:
            continue
        seen.add(name.casefold())
        themes = ({'culture':0,'history':0,'nature':1,'food':0} if park else
                  {'culture':.6,'history':.4,'nature':0,'food':0})
        candidates.append({
            'id':f'osm_{element.tag}_{element.attrib["id"]}', 'destination_id':city['id'], 'name':name,
            'latitude':coordinates[0], 'longitude':coordinates[1], 'themes':themes,
            'duration_min':30 if park else 60, 'duration_max':120, 'indoor':None,
            'admission_rub':None, 'opening_hours':tags.get('opening_hours'), 'verified':False,
            'provenance':{'source_url':f'https://www.openstreetmap.org/{element.tag}/{element.attrib["id"]}',
                'observed_at':metadata['observed_at'], 'valid_from':metadata['observed_at'][:10],
                'valid_to':metadata['observed_at'][:10], 'data_kind':'osm', 'version':'1',
                'confidence_note':'OSM candidate; themes/duration are expert defaults; official price and hours unverified'},
            '_category':'park' if park else category, '_website':tags.get('website', tags.get('contact:website'))})
    candidates.sort(key=lambda p: (p['_category'] != 'park', not bool(p['_website']), p['name']))
    selected = candidates[:30]
    editorial = [{k:v for k,v in p.items() if k.startswith('_') or k in ('id','name')} for p in selected]
    selected = [{k:v for k,v in p.items() if not k.startswith('_')} for p in selected]
    write_json(ROOT / f'data/processed/pois/{city["id"]}.json', selected)
    write_json(ROOT / f'data/processed/pois/{city["id"]}.review.json', editorial)
    if not graph:
        raise ValueError('No pedestrian network')
    snapped = []
    for point in selected:
        coordinates = (point['latitude'], point['longitude'])
        nearest = min(graph, key=lambda node: distance_meters(coordinates, nodes[node]))
        offset = distance_meters(coordinates, nodes[nearest])
        snapped.append((nearest, offset))
    matrix = []
    for i, (node, offset) in enumerate(snapped):
        distances = shortest_distances(graph, node)
        matrix.append([0 if i == j else (round(offset + distances[other] + other_offset, 1)
                       if other in distances and offset <= 100 and other_offset <= 100 else None)
                       for j, (other, other_offset) in enumerate(snapped)])
    write_json(ROOT / f'data/processed/matrices/{city["id"]}.json', {
        'poi_ids':[p['id'] for p in selected], 'profile':'experimental_foot', 'source':'OSM local extract',
        'method':'Dijkstra on bidirectional walkable ways; straight connectors <=100m',
        'distance_meters':matrix,
        'duration_seconds':[[None if x is None else math.ceil(x/ (4/3.6)) for x in row] for row in matrix],
        'graph_version':metadata['observed_at'], 'snap_meters':[round(s[1],1) for s in snapped],
        'data_kind':'expert', 'confirmed':False,
        'limitations':['Not OSRM; missing barriers, turn restrictions and traffic signals',
                       'Truncated city-center graph; cannot prove real-world unreachability',
                       '4 km/h expert walking speed; connectors not verified paths']})
    return {'destination':city['id'], 'candidates_in_extract':len(candidates), 'selected':len(selected),
            'confirmed_pois':0, 'network_nodes':len(graph),
            'unreachable_or_unsnapped_pairs':sum(x is None for row in matrix for x in row),
            'matrix_pairs':len(selected)**2}

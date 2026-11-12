from pathlib import Path

from rdflib import Graph, Literal, Namespace, RDF, RDFS, OWL, XSD, BNode

from .data import ROOT


TRAVEL = Namespace('https://example.org/travel-dss#')


def export_ontology(snapshot, target: Path = ROOT/'data/knowledge/travel.owl') -> dict:
    graph = Graph()
    graph.bind('travel',TRAVEL)
    graph.bind('owl',OWL)
    graph.add((TRAVEL.ontology,RDF.type,OWL.Ontology))
    graph.add((TRAVEL.ontology,RDFS.comment,Literal('Демонстрационная онтология СППР; цены и расписания синтетические.',lang='ru')))
    classes = {'Destination':'Направление','POI':'Объект посещения','IndoorPOI':'Объект в помещении',
        'OutdoorPOI':'Объект на открытом воздухе','SyntheticPOI':'Демонстрационный объект',
        'NeedsConfirmationPOI':'Объект, требующий подтверждения','Offer':'Предложение',
        'TransportOffer':'Транспорт','StayOffer':'Жилье','TripWindow':'Период поездки'}
    for name,label in classes.items():
        graph.add((TRAVEL[name],RDF.type,OWL.Class))
        graph.add((TRAVEL[name],RDFS.label,Literal(label,lang='ru')))
    for child,parent in [('IndoorPOI','POI'),('OutdoorPOI','POI'),('SyntheticPOI','POI'),
                          ('NeedsConfirmationPOI','POI'),('TransportOffer','Offer'),('StayOffer','Offer')]:
        graph.add((TRAVEL[child],RDFS.subClassOf,TRAVEL[parent]))
    graph.add((TRAVEL.SyntheticPOI,RDFS.subClassOf,TRAVEL.NeedsConfirmationPOI))
    graph.add((TRAVEL.IndoorPOI,OWL.disjointWith,TRAVEL.OutdoorPOI))
    for name,domain,range_name in [('locatedIn','POI','Destination'),('offeredFor','Offer','Destination')]:
        graph.add((TRAVEL[name],RDF.type,OWL.ObjectProperty))
        graph.add((TRAVEL[name],RDFS.domain,TRAVEL[domain]))
        graph.add((TRAVEL[name],RDFS.range,TRAVEL[range_name]))
    for name,kind in [('priceRub',XSD.decimal),('sourceKind',XSD.string),('confirmationRequired',XSD.boolean)]:
        graph.add((TRAVEL[name],RDF.type,OWL.DatatypeProperty))
        graph.add((TRAVEL[name],RDFS.range,kind))
    restriction = BNode()
    graph.add((restriction,RDF.type,OWL.Restriction))
    graph.add((restriction,OWL.onProperty,TRAVEL.confirmationRequired))
    graph.add((restriction,OWL.hasValue,Literal(True)))
    graph.add((TRAVEL.NeedsConfirmationPOI,RDFS.subClassOf,restriction))
    for destination in snapshot.destinations:
        node = TRAVEL[destination.id]
        graph.add((node,RDF.type,TRAVEL.Destination))
        graph.add((node,RDFS.label,Literal(destination.name,lang='ru')))
    for poi in snapshot.pois:
        node = TRAVEL[poi.id]
        graph.add((node,RDF.type,TRAVEL.POI))
        if poi.indoor is not None:
            graph.add((node,RDF.type,TRAVEL.IndoorPOI if poi.indoor else TRAVEL.OutdoorPOI))
        if poi.provenance.data_kind.value == 'synthetic':
            graph.add((node,RDF.type,TRAVEL.SyntheticPOI))
        graph.add((node,RDFS.label,Literal(poi.name,lang='ru')))
        graph.add((node,TRAVEL.locatedIn,TRAVEL[poi.destination_id]))
        graph.add((node,TRAVEL.sourceKind,Literal(poi.provenance.data_kind.value)))
        if poi.admission_rub is not None:
            graph.add((node,TRAVEL.priceRub,Literal(str(poi.admission_rub),datatype=XSD.decimal)))
    for offers,kind in [(snapshot.transport_offers,'TransportOffer'),(snapshot.stay_offers,'StayOffer')]:
        for offer in offers:
            node = TRAVEL[offer.quote.id]
            graph.add((node,RDF.type,TRAVEL[kind]))
            graph.add((node,TRAVEL.offeredFor,TRAVEL[offer.quote.destination_id]))
            graph.add((node,TRAVEL.priceRub,Literal(str(offer.quote.total_rub),datatype=XSD.decimal)))
    for window in snapshot.trip_windows:
        graph.add((TRAVEL[window.id],RDF.type,TRAVEL.TripWindow))
    target.parent.mkdir(parents=True,exist_ok=True)
    graph.serialize(destination=str(target),format='xml')
    return {'triples':len(graph),'classes':len(classes),'pois':len(snapshot.pois),
            'offers':len(snapshot.transport_offers)+len(snapshot.stay_offers),
            'reasoner_check':'SyntheticPOI subclass NeedsConfirmationPOI; confirmationRequired hasValue true'}

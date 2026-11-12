"""Verify OWL entailments with the HermiT and OWL API shipped in Protégé."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

from travel_dss.data import ROOT, write_json

SOURCE = '''
import java.io.File;
import org.semanticweb.owlapi.apibinding.OWLManager;
import org.semanticweb.owlapi.model.*;
import org.semanticweb.HermiT.Reasoner;
class CheckTravelOntology {
 public static void main(String[] args) throws Exception {
  var manager = OWLManager.createOWLOntologyManager();
  var ontology = manager.loadOntologyFromOntologyDocument(new File(args[0]));
  var reasoner = new Reasoner.ReasonerFactory().createReasoner(ontology);
  var required = manager.getOWLDataFactory().getOWLClass(IRI.create("https://example.org/travel-dss#NeedsConfirmationPOI"));
  int inferred = reasoner.getInstances(required,false).getFlattened().size();
  if(!reasoner.isConsistent() || inferred != 173) throw new IllegalStateException("Inference failed: "+inferred);
  System.out.println("TRAVEL_RESULT {\\"consistent\\":true,\\"inferred_needs_confirmation\\":"+inferred+",\\"asserted_needs_confirmation\\":"+ontology.getClassAssertionAxioms(required).size()+",\\"axioms\\":"+ontology.getAxiomCount()+"}");
  reasoner.dispose();
 }
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--protege-home',type=Path,required=True,help='Каталог распакованного Protégé 5.6.9')
    parser.add_argument('--java',default=shutil.which('java'))
    args = parser.parse_args()
    distribution = args.protege_home.resolve()
    work = ROOT/'.runtime/protege-check'
    work.mkdir(parents=True,exist_ok=True)
    source = work/'CheckTravelOntology.java'
    source.write_text(SOURCE)
    hermit = distribution/'plugins/org.semanticweb.hermit-1.4.3.456.jar'
    for jar in [distribution/'bundles/owlapi-osgidistribution.jar',hermit]:
        with zipfile.ZipFile(jar) as archive:
            for name in archive.namelist():
                if name.endswith('.jar'):
                    (work/Path(name).name).write_bytes(archive.read(name))
    classpath = ':'.join([str(distribution/'bundles/*'),str(hermit),str(work/'*')])
    result = subprocess.run([args.java,'-Djava.awt.headless=true','-cp',classpath,str(source),
                             str(ROOT/'data/knowledge/travel.owl')],text=True,capture_output=True,check=True)
    line = next(line for line in result.stdout.splitlines() if line.startswith('TRAVEL_RESULT '))
    report = {'protege_version':'5.6.9','engine':'OWL API 4.5.29 + HermiT 1.4.3.456 from Protégé',
              'verification':'headless inference; ontology also loaded in desktop Protégé',**json.loads(line[14:])}
    write_json(ROOT/'reports/passport/protege_check.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()

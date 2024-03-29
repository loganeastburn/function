import sys
import logging
logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

import re
from collections import defaultdict
from idmap import IDMap
from gmt import GMT
import urllib2


class go:
    heads = None
    go_terms = None
    alt_id2std_id = None
    populated = None
    s_orgs = None

    # populate this field if you want to mark this GO as organism specific
    go_organism_tax_id = None

    """
    Pass the obo file
    """

    def __init__(self, obo_file=None):
        self.heads = []
        self.go_terms = {}
        self.go_obsolete = {}
        self.alt_id2std_id = {}
        self.name2synonyms = {}
        self.populated = False
        self.s_orgs = []
        self.__meta = {}

        if obo_file:
            self.load_obo(obo_file)

    def load_obo(self, obo_file, remote_location=False, timeout=5):

        if remote_location:
            obo = urllib2.urlopen(obo_file, timeout=timeout)
            lines = obo.readlines()
        else:
            obo = open(obo_file, 'r')
            lines = obo.readlines()

        inside = False
        gterm = None
        for line in lines:
            fields = line.rstrip().split()

            if len(fields) < 1:
                continue

            elif not inside and not len(self.go_terms.keys()) and len(fields) > 1:
                key = fields[0]
                if key.endswith(':'):
                    key = key[:-1]
                self.__meta[key] = fields[1]
            elif fields[0] == '[Term]':
                if gterm:
                    if gterm.head:
                        self.heads.append(gterm)
                inside = True
            elif fields[0] == '[Typedef]':
                if gterm:
                    if gterm.head:
                        self.heads.append(gterm)
                inside = False

            elif inside and fields[0] == 'id:':
                # print fields[1]
                if fields[1] in self.go_terms:
                    gterm = self.go_terms[fields[1]]
                else:
                    gterm = GOTerm(fields[1])
                    self.go_terms[gterm.get_id()] = gterm
                # print self.go_terms[fields[1]]
            elif inside and fields[0] == 'name:':
                fields.pop(0)
                gterm.fullname = ' '.join(fields)
                name = '_'.join(fields)
                name = name.replace('\'', '')
                name = re.sub('[^\w\s_-]', '_', name).strip().lower()
                name = re.sub('[-\s_]+', '_', name)
                gterm.name = name
            elif inside and fields[0] == 'namespace:':
                gterm.namespace = fields[1]
            elif inside and fields[0] == 'def:':
                gterm.desc = ' '.join(fields[1:]).split('\"')[1]
            elif inside and fields[0] == 'alt_id:':
                gterm.alt_id.append(fields[1])
                self.alt_id2std_id[fields[1]] = gterm.get_id()
            elif inside and fields[0] == 'is_a:':
                gterm.head = False
                fields.pop(0)
                pgo_id = fields.pop(0)
                if pgo_id not in self.go_terms:
                    self.go_terms[pgo_id] = GOTerm(pgo_id)

                gterm.is_a.append(self.go_terms[pgo_id])
                self.go_terms[pgo_id].parent_of.add(gterm)
                gterm.child_of.add(self.go_terms[pgo_id])
            elif inside and fields[0] == 'relationship:':
                if fields[1].find('has_part') != -1:
                    # has part is not a parental relationship -- it is actually
                    # for children.
                    continue
                gterm.head = False
                pgo_id = fields[2]
                if pgo_id not in self.go_terms:
                    self.go_terms[pgo_id] = GOTerm(pgo_id)
                # Check which relationship you are with this parent go term
                if fields[1] == 'regulates' or fields[
                        1] == 'positively_regulates' or fields[1] == 'negatively_regulates':
                    gterm.relationship_regulates.append(self.go_terms[pgo_id])
                elif fields[1] == 'part_of':
                    gterm.relationship_part_of.append(self.go_terms[pgo_id])
                else:
                    logger.info(
                        "Unknown relationship %s",
                        self.go_terms[pgo_id].name)
                    continue
                self.go_terms[pgo_id].parent_of.add(gterm)
                gterm.child_of.add(self.go_terms[pgo_id])
            elif inside and fields[0] == 'is_obsolete:':
                gterm.head = False
                del self.go_terms[gterm.get_id()]
                gterm.obsolete = True
                self.go_obsolete[gterm.get_id()] = gterm
            elif inside and fields[0] == 'synonym:':
                syn = ' '.join(fields[1:]).split('\"')[1]
                syn = syn.replace('lineage name: ', '')
                gterm.synonyms.append(syn)
                if gterm.name in self.name2synonyms:
                    self.name2synonyms[gterm.name].append(syn)
                else:
                    self.name2synonyms[gterm.name] = [syn]
            elif inside and fields[0] == 'xref:':
                tok = fields[1].split(':')
                if len(tok) > 1:
                    (xrefdb, xrefid) = fields[1].split(':')[0:2]
                    gterm.xrefs.setdefault(xrefdb, set()).add(xrefid)

        return True

    """
    propagate all gene annotations
    """

    def propagate(self):
        logger.info("Propagate gene annotations")
        for head_gterm in self.heads:
            logger.info("Propagating %s", head_gterm.name)
            self.propagate_recurse(head_gterm)

    def propagate_recurse(self, gterm):
        if not len(gterm.parent_of):
            logger.debug("Base case with term %s", gterm.name)
            return

        for child_term in gterm.parent_of:
            self.propagate_recurse(child_term)
            new_annotations = set()

            regulates_relation = (gterm in child_term.relationship_regulates)
            part_of_relation = (gterm in child_term.relationship_part_of)

            for annotation in child_term.annotations:
                copied_annotation = None
                # if this relation with child is a regulates(and its sub class)
                # filter annotations
                if regulates_relation:
                    # only add annotations that didn't come from a part of or
                    # regulates relationship
                    if annotation.ready_regulates_cutoff:
                        continue
                    else:
                        copied_annotation = annotation.prop_copy(
                            ready_regulates_cutoff=True)
                elif part_of_relation:
                    copied_annotation = annotation.prop_copy(
                        ready_regulates_cutoff=True)
                else:
                    copied_annotation = annotation.prop_copy()

                new_annotations.add(copied_annotation)
            gterm.annotations = gterm.annotations | new_annotations

    """
    summarize gene annotations for an organism (i.e. to load multiple organisms for output of annotation numbers to json)
    """

    def summarize(self, org):
        self.s_orgs.append(org)
        for (name, term) in self.go_terms.iteritems():
            tgenes = set()
            dgenes = set()
            for annotation in term.annotations:
                tgenes.add(annotation.gid)
                if annotation.direct:
                    dgenes.add(annotation.gid)
                del annotation
            term.annotations = set([])
            if term.summary is None:
                term.summary = {}
            term.summary[org] = {"d": len(dgenes), "t": len(tgenes)}
            term.summary['nparents'] = len(term.child_of)
            term.summary['go_id'] = term.go_id

    """
    add "sstr" to summary for term based on whether the term is in sset
    """

    def summarize_flag(self, sset, sstr):
        for item in sset:
            try:
                tid = self.alt_id2std_id[item]
            except KeyError:
                tid = item
            try:
                term = self.go_terms[tid]
                term.summary[sstr] = True
            except KeyError:
                import sys
                sys.stderr.write(tid + '\n')

    """
    write slim starting from a dfs at the term with the id root  with nvotes to ofile
    """

    def write_slim(self, head, nvotes, ofile):
        root = self.go_terms[head]
        written = set([])
        pruned = set([])
        self.vote_write(root, nvotes, ofile, written, pruned)

        # remove terms that were pruned in another branch
        written -= pruned

        for term in written:
            ofile.write(term.name + '\t' + term.go_id + '\n')

    """
    recurse for pruned terms
    """

    def pruned(self, term, pruned):
        for child in term.parent_of:
            pruned.add(child)
            self.pruned(child, pruned)

    """
    prune all gene annotations, if nstr is passed, instead of pruning, add a flag to summary of "namestr" if the node meets these criteria.
    """

    def prune(self, eval_str, nstr=None):
        dterms = set()
        heads = set(self.heads)
        for (name, term) in self.go_terms.iteritems():

            total = len(term.annotations)
            direct = 0
            nparents = len(term.child_of)
            for annotation in term.annotations:
                if annotation.direct:
                    direct += 1

            dmax = direct
            tmax = total
            num_children = len(term.parent_of)
            if term.summary:
                if self.s_orgs:
                    dmax = max([term.summary[org]["d"] for org in self.s_orgs])
                    tmax = max([term.summary[org]["t"] for org in self.s_orgs])
                if 'max' not in term.summary:
                    term.summary['max'] = {}
                term.summary['max']['d'] = dmax
                term.summary['max']['t'] = tmax

                if 'desc' not in term.summary:
                    term.summary['desc'] = term.desc

                if 'goid' not in term.summary:
                    term.summary['goid'] = term.go_id

            if term in heads:
                continue
            prune = eval(eval_str)
            if nstr and prune:
                term.summary[nstr] = True
            elif prune:
                if term.summary is not None:
                    try:
                        sstatus = term.summary['slim']
                        if sstatus:
                            import sys
                            sys.stderr.write("Pruned slim term: (" +
                                             term.go_id +
                                             ") " +
                                             term.name +
                                             "\t" +
                                             str(term.summary) +
                                             "\n")
                    except KeyError:
                        pass
                for pterm in term.child_of:
                    pterm.parent_of.update(term.parent_of)
                    pterm.parent_of.discard(term)
                for cterm in term.parent_of:
                    cterm.child_of.update(term.child_of)
                    cterm.child_of.discard(term)
                dterms.add(name)
        for name in dterms:
            del self.go_terms[name]
        # remove connections to root if there are other parents
        for (name, term) in self.go_terms.iteritems():
            # if there is something in the intersection
            intersection = term.child_of & heads
            if (intersection):
                # if the intersection isn't the only thing it's a child of
                if (term.child_of - heads):
                    term.child_of -= intersection
                    for hterm in intersection:
                        hterm.parent_of.remove(term)

    def get_term(self, tid):
        logger.debug('get_term: %s', tid)
        term = None
        try:
            term = self.go_terms[tid]
        except KeyError:
            try:
                term = self.go_terms[self.alt_id2std_id[tid]]
            except KeyError:
                logger.error('Term name does not exist: %s', tid)
        return term

    def get_meta_data(self, key):
        if key in self.__meta:
            return self.__meta[key]
        else:
            return None

    def get_termobject_list(self, terms=None, p_namespace=None):
        logger.info('get_termobject_list')
        if terms is None:
            terms = self.go_terms.keys()
        reterms = []
        for tid in terms:
            obo_term = self.get_term(tid)
            if obo_term is None:
                continue
            if p_namespace is not None and obo_term.namespace != p_namespace:
                continue
            reterms.append(obo_term)
        return reterms

    def get_obsolete_terms(self, p_namespace=None):
        logger.info('get_obsolete_list')
        return self.go_obsolete.values()

    def get_termdict_list(self, terms=None, p_namespace=None):
        logger.info('get_termdict_list')
        tlist = self.get_termobject_list(terms=terms, p_namespace=p_namespace)
        reterms = []
        for obo_term in tlist:
            reterms.append({'oboid': obo_term.go_id, 'name': obo_term.name})
        return reterms

    def get_xref_mapping(self, prefix):
        xrefs = defaultdict(set)
        for term in self.get_termobject_list():
            ids = term.get_xrefs(prefix)
            if ids:
                for xref in ids:
                    xrefs[xref].add(term.go_id)
        return xrefs

    def as_gmt(self):
        gmt = GMT()
        tlist = sorted(self.get_termobject_list())
        for term in tlist:
            if len(term.annotations):
                gmt.add_geneset(gsid=term.go_id, name=term.name)
            for annotation in term.annotations:
                gmt.add_gene(term.go_id, annotation.gid)
        return gmt

    def print_terms(self, out_dir, terms=None, p_namespace=None):
        logger.info('print_terms')
        tlist = self.get_termobject_list(terms=terms, p_namespace=p_namespace)
        # print terms
        for term in tlist:
            id_set = set()  # put things into a set to avoid duplicate entries (possible multiple annotations with single ID)
            for annotation in term.annotations:
                id_set.add(annotation.gid)
            if len(id_set) == 0:
                continue
            output_fh = open(out_dir + '/' + term.name, 'w')
            # keep previous behavior w/ newline at end
            output_fh.write('\n'.join(id_set) + '\n')
            output_fh.close()

    def print_to_single_file(self, out_file, terms=None,
                             p_namespace=None, gene_asso_format=False):
        logger.info('print_to_single_file')
        tlist = sorted(
            self.get_termobject_list(
                terms=terms,
                p_namespace=p_namespace))
        f = open(out_file, 'w')
        for term in tlist:
            for annotation in term.annotations:
                if gene_asso_format:
                    to_print = [annotation.xdb if annotation.xdb else '',
                                annotation.gid if annotation.gid else '',
                                '', '',  # Gene Symbol, NOT/''
                                term.go_id if term.go_id else '',
                                annotation.ref if annotation.ref else '',
                                annotation.evidence if annotation.evidence else '',
                                annotation.date if annotation.date else '',
                                str(annotation.direct),
                                # Direct is added in to indicate prop status
                                # cross annotated is added in to indicate cross
                                # status
                                str(annotation.cross_annotated),
                                # if cross annotated, where the annotation is
                                # from
                                annotation.origin if annotation.cross_annotated else '',
                                str(annotation.ortho_evidence) if annotation.ortho_evidence else '', '', '']  # if cross annotated, then the evidence of the cross_annotation (e.g. bootstrap value, p-value)
                    print >> f, '\t'.join([str(x) for x in to_print])
                else:
                    print >> f, term.go_id + '\t' + term.name + '\t' + annotation.gid
        f.close()

    def print_to_gmt_file(self, out_file, terms=None, p_namespace=None):
        logger.info('print_to_gmt_file')
        tlist = sorted(
            self.get_termobject_list(
                terms=terms,
                p_namespace=p_namespace))
        f = open(out_file, 'w')
        for term in tlist:
            genes = set()
            for annotation in term.annotations:
                genes.add(annotation.gid)
            if len(genes) > 0:
                print >> f, term.go_id + '\t' + term.name + \
                    ' (' + str(len(genes)) + ')\t' + '\t'.join(genes)
        f.close()

    def print_to_mat_file(self, out_file, terms=None, p_namespace=None):
        logger.info('print_to_mat_file')
        tlist = sorted(
            self.get_termobject_list(
                terms=terms,
                p_namespace=p_namespace))
        f = open(out_file, 'w')

        allgenes = set()
        genedict = defaultdict(set)
        termlist = []
        for term in tlist:
            if len(term.annotations) == 0:
                continue

            termlist.append(term.go_id)

            for annotation in term.annotations:
                allgenes.add(annotation.gid)
                genedict[annotation.gid].add(term.go_id)

        print >> f, '\t' + '\t'.join(termlist)
        for g in list(allgenes):
            row = []
            row.append(g)
            for termid in termlist:
                row.append('1' if termid in genedict[g] else '0')
            print >> f, '\t'.join(row)
        f.close()

    # print each term ref IDs to a standard out
    def print_refids(self, terms=None, p_namespace=None):
        logger.info('print_refids')
        tlist = sorted(
            self.get_termobject_list(
                terms=terms,
                p_namespace=p_namespace))
        for term in tlist:
            for annotation in term.annotations:
                print term.go_id + '\t' + annotation.ref + '\t' + annotation.gid

    # be aware this is added only to be used with python script
    # cross_annotate_single_file_only_crossed.py
    def print_to_single_file_cross_annotated(
            self, out_file, terms=None, p_namespace=None):
        logger.info('print_to_single_file_cross_annotated')
        tlist = sorted(
            self.get_termobject_list(
                terms=terms,
                p_namespace=p_namespace))
        f = open(out_file, 'w')
        for term in tlist:
            for gene in term.cross_annotated_genes:
                print >> f, gene + '\t' + term.go_id
        f.close()

    def to_json(self, head_id=None):
        """
        Return the hierarchy for all nodes with more than min genes
        as a json string (depends on simplejson).
        """
        import simplejson
        redict = {}
        if head_id is not None:
            head = self.go_terms[head_id]
            self.dictify(head, redict)
        else:
            for head in self.heads:
                self.dictify(head, redict)
        return 'var ontology = ' + simplejson.dumps(redict, indent=2)

    def dictify(self, term, thedict):
        if not term.summary:
            direct = 0
            total = len(term.annotations)
            for annotation in term.annotations:
                if annotation.direct:
                    direct += 1
        child_vals = []
        for child in term.parent_of:
            cdict = {}
            self.dictify(child, cdict)
            child_vals.append(cdict)
        thedict["name"] = term.name
        if not term.summary:
            thedict["direct"] = direct
            thedict["total"] = total
        else:
            thedict["summary"] = term.summary
        if child_vals:
            thedict["children"] = child_vals
        return

    def map_genes(self, id_name):
        for go_term in self.go_terms.itervalues():
            go_term.map_genes(id_name)

    def populate_annotations(self, annotation_file, xdb_col=0,
                             gene_col=None, term_col=None, ref_col=5, ev_col=6, date_col=13):
        logger.info('Populate gene annotations: %s', annotation_file)
        details_col = 3
        f = open(annotation_file, 'r')
        for line in f:
            if line[0] == '!':
                continue
            fields = line.rstrip('\n').split('\t')

            xdb = fields[xdb_col]
            gene = fields[gene_col]
            go_id = fields[term_col]

            try:
                ref = fields[ref_col]
            except IndexError:
                ref = None
            try:
                ev = fields[ev_col]
            except IndexError:
                ev = None
            try:
                date = fields[date_col]
            except IndexError:
                date = None

            if date_col < len(fields):
                date = fields[date_col]
            else:
                date = None

            try:
                details = fields[details_col]
                if details == 'NOT':
                    continue
            except IndexError:
                pass
            go_term = self.get_term(go_id)
            if go_term is None:
                continue
            logger.info('Gene %s and term %s', gene, go_term.go_id)
            annotation = Annotation(
                xdb=xdb,
                gid=gene,
                ref=ref,
                evidence=ev,
                date=date,
                direct=True)
            go_term.annotations.add(annotation)

        f.close()
        self.populated = True

    def add_annotation(self, go_id, gid, ref, direct):
        go_term = self.get_term(go_id)
        if not go_term:
            return False
        annot = Annotation(xdb=None, gid=gid, direct=direct, ref=ref)
        go_term.annotations.add(annot)
        return True

    def populate_additional_taxon_specificity(
            self, ncbi_tax_obj, taxon_specificity_add_file, tag_tax_id):
        logger.info("Populate GO specificity: %s", taxon_specificity_add_file)

        f = open(taxon_specificity_add_file, 'r')

        if tag_tax_id in ncbi_tax_obj.id2species:
            self.go_organism_tax_id = tag_tax_id
        else:
            logger.error("NCBI tax ID %s does not exist", tag_tax_id)
            sys.exit(1)

        for line in f:
            fields = line.rstrip('\n').split('\t')
            if len(fields) == 0:
                continue

            if line[0] == '#':
                continue

            gid = fields[0]
            relationship = fields[1]
            org = fields[2]
            # now go label your go tree
            self.propagate_taxon_specificity(
                [org], gid, relationship, ncbi_tax_obj)

        f.close()

    def populate_taxon_specificity(
            self, ncbi_tax_obj, taxon_specificity_obo_file, tag_tax_id):
        logger.info("Populate GO specificity: %s", taxon_specificity_obo_file)
        f = open(taxon_specificity_obo_file, 'r')
        if tag_tax_id in ncbi_tax_obj.id2species:
            self.go_organism_tax_id = tag_tax_id
        else:
            logger.error("NCBI tax ID %s does not exist", tag_tax_id)
            sys.exit(1)

        inside = False
        gid = None
        relationship = None
        tax_id = None

        only_in_taxon = set([])
        never_in_taxon = set([])
        for line in f:
            fields = line.rstrip('\n').split()
            if len(fields) == 0:
                continue

            if fields[0] == '[Term]':
                inside = True
            elif inside and fields[0] == 'id:':
                gid = fields[1]
            elif inside and fields[0] == 'relationship:':
                relationship = fields[1]
                (tax_type, tax_id) = fields[2].split(':')

                final_tax_ids = []
                if tax_type == 'NCBITaxon':
                    final_tax_ids.append(tax_id)
                elif tax_type == 'NCBITaxon_Union':
                    for i, fl in enumerate(fields):
                        if not (i > 3 and fl != 'or'):
                            continue

                        if fl in ncbi_tax_obj.species2id:
                            final_tax_ids.append(ncbi_tax_obj.species2id[fl])
                        elif fl in ncbi_tax_obj.in_part:
                            [final_tax_ids.append(
                                in_part_id) for in_part_id in ncbi_tax_obj.in_part[fl]]
                        else:
                            logger.error("Missing NCBI tax ID: %s", fl)
                # now go label your go tree
                self.propagate_taxon_specificity(
                    final_tax_ids, gid, relationship, ncbi_tax_obj)

                # ok now collected all info
                inside = False
                gid = None
                relationship = None
                tax_id = None

        f.close()

    def propagate_taxon_specificity(
            self, tax_ids, term_id, relationship, ncbi_tax_obj):
        current_gterm = self.get_term(term_id)
        if current_gterm is None:
            return

        if relationship == 'only_in_taxon':
            for tid in tax_ids:
                if ncbi_tax_obj.check_lineage(tid, self.go_organism_tax_id):
                    return
            self.propagate_taxon_set_false(term_id)
        elif relationship == 'never_in_taxon':
            for tid in tax_ids:
                if ncbi_tax_obj.check_lineage(tid, self.go_organism_tax_id):
                    self.propagate_taxon_set_false(term_id)
                    return
        else:
            logger.error('Invalid relationship term: %s', relationship)
            return

    def propagate_taxon_set_false(self, tid):
        go_term = self.get_term(tid)
        if go_term is None:
            return

        go_term.valid_go_term = False

        for child_term in go_term.parent_of:
            self.propagate_taxon_set_false(child_term.get_id())

    # check if slim terms forms a true fringe in the obo structure
    def check_fringe(self, slim_file, namespace=None):
        leaf_tids = []
        slim_tids = []

        missing_leaves = open("leaves_missing_from_slim.txt", 'w')
        missing_out = open("all_missing.txt", 'w')
        # add GO ids to the leaf terms
        for tid in self.go_terms.keys():
            leaf_term = self.go_terms[tid]
            if len(leaf_term.parent_of) == 0:
                if namespace is not None and leaf_term.namespace != namespace:
                    continue
                leaf_term.annotations.add(Annotation(gid=tid))
                leaf_tids.append(tid)

        # now propagate the GO ids from the leaf terms
        self.propagate()

        # open go terms from slim term
        f = open(slim_file, 'r')
        stids = []
        for line in f:
            fields = line.rstrip('\n').split('\t')
            stids.append(fields[1])
        f.close()

        # now go colect the GO leaf term ids that have been propagated to the
        # slim terms
        for tid in stids:
            slim_term = self.get_term(tid)
            if slim_term is None:
                logger.error(
                    'Slim term name does not exist (potentially obsolete term): %s', tid)
                continue
            slim_tids.extend(
                [annotation.gid for annotation in slim_term.annotations])

        # now compare two sets
        leaf_tids.sort()
        slim_tids.sort()

        if leaf_tids == slim_tids:
            return True
        else:
            for lgoterm in leaf_tids:
                if lgoterm not in slim_tids:
                    logger.warning("Missing leaf terms: %s", lgoterm)
                    missing_leaves.write(
                        lgoterm + '\t' + gene_ontology.go_terms[lgoterm].name + '\n')
                    missing_out.write(
                        lgoterm + '\t' + gene_ontology.go_terms[lgoterm].name + '\n')
                    miss_l_ancest = self.get_ancestors(lgoterm)
                    for m in miss_l_ancest:
                        missing_out.write(
                            m + '\t' + gene_ontology.go_terms[m].name + '\n')
            return False

    """
    get propagated descendents of term
    """

    def get_descendents(self, gterm):

        if gterm not in self.go_terms:
            return set()
        term = self.go_terms[gterm]

        if len(term.parent_of) == 0:
            return set()

        child_terms = set()
        for child_term in term.parent_of:
            if child_term.namespace != term.namespace:
                continue
            child_terms.add(child_term.go_id)
            child_terms = child_terms | self.get_descendents(child_term.go_id)

        return child_terms

    """
    get propagated ancestors of term
    """

    def get_ancestors(self, gterm):
        if (gterm in self.go_terms) is False:
            return set()
        term = self.go_terms[gterm]

        if len(term.child_of) == 0:
            return set()

        parent_terms = set()
        for parent_term in term.child_of:
            if parent_term.namespace != term.namespace:
                continue
            parent_terms.add(parent_term.go_id)
            parent_terms = parent_terms | self.get_ancestors(parent_term.go_id)

        return parent_terms

    """
    Return a set of leaf terms in ontology
    """

    def get_leaves(self, namespace='biological_process', min_annot=10):
        leaves = set()
        bottom = set()
        for term in self.go_terms.values():
            if len(term.parent_of) == 0 and term.namespace == namespace and len(
                    term.annotations) >= min_annot:
                leaves.add(term)
        return leaves


class Annotation(object):

    def __init__(self, xdb=None, gid=None, ref=None, evidence=None, date=None, direct=False,
                 cross_annotated=False, origin=None, ortho_evidence=None, ready_regulates_cutoff=False):
        super(Annotation, self).__setattr__('xdb', xdb)
        super(Annotation, self).__setattr__('gid', gid)
        super(Annotation, self).__setattr__('ref', ref)
        super(Annotation, self).__setattr__('evidence', evidence)
        super(Annotation, self).__setattr__('date', date)
        super(Annotation, self).__setattr__('direct', direct)
        super(Annotation, self).__setattr__('cross_annotated', cross_annotated)
        super(Annotation, self).__setattr__('origin', origin)
        super(Annotation, self).__setattr__('ortho_evidence', ortho_evidence)
        super(
            Annotation,
            self).__setattr__(
            'ready_regulates_cutoff',
            ready_regulates_cutoff)

    def prop_copy(self, ready_regulates_cutoff=None):
        if ready_regulates_cutoff is None:
            ready_regulates_cutoff = self.ready_regulates_cutoff

        return Annotation(xdb=self.xdb, gid=self.gid, ref=self.ref,
                          evidence=self.evidence, date=self.date, direct=False, cross_annotated=False,
                          ortho_evidence=self.ortho_evidence, ready_regulates_cutoff=ready_regulates_cutoff)

    def __hash__(self):
        return hash((self.xdb, self.gid, self.ref, self.evidence, self.date,
                     self.direct, self.cross_annotated, self.ortho_evidence,
                     self.ready_regulates_cutoff, self.origin))

    def __eq__(self, other):
        return (self.xdb, self.gid, self.ref, self.evidence, self.date,
                self.direct, self.cross_annotated, self.ortho_evidence,
                self.ready_regulates_cutoff, self.origin).__eq__((other.xdb,
                                                                  other.gid, other.ref, other.evidence, other.date,
                                                                  other.direct, other.cross_annotated, other.ortho_evidence,
                                                                  other.ready_regulates_cutoff, other.origin))

    def __setattr__(self, *args):
        raise TypeError("Attempt to modify immutable object.")
    __delattr__ = __setattr__


class GOTerm:
    go_id = ''
    is_a = None
    relationship = None
    parent_of = None
    child_of = None
    annotations = None
    alt_id = None
    namespace = ''
    included_in_all = None
    valid_go_term = None
    cross_annotated_genes = None
    head = None
    name = None
    base_counts = None
    counts = None
    summary = None
    desc = None
    votes = None
    synonyms = None
    fullname = None
    xrefs = None
    obsolete = None

    def __init__(self, go_id):
        self.head = True
        self.go_id = go_id
        self.annotations = set([])
        self.cross_annotated_genes = set([])
        self.is_a = []
        self.relationship_regulates = []
        self.relationship_part_of = []
        self.parent_of = set()
        self.child_of = set()
        self.alt_id = []
        self.included_in_all = True
        self.valid_go_term = True
        self.name = None
        self.base_counts = None
        self.counts = None
        self.desc = None
        self.votes = set([])
        self.synonyms = []
        self.fullname = None
        self.xrefs = {}
        self.obsolete = False

    def __cmp__(self, other):
        return cmp(self.go_id, other.go_id)

    def __hash__(self):
        return(self.go_id.__hash__())

    def __repr__(self):
        return(self.go_id + ': ' + self.name)

    def get_id(self):
        return self.go_id

    def map_genes(self, id_name):
        mapped_annotations_set = set([])
        for annotation in self.annotations:
            mapped_genes = id_name.get(annotation.gid)
            if mapped_genes is None and 'CELE_' in annotation.gid:
                mapped_genes = id_name.get(
                    annotation.gid[5:len(annotation.gid)])

            if mapped_genes is None:
                logger.warning('No matching gene id: %s', annotation.gid)
                continue
            for mgene in mapped_genes:
                mapped_annotations_set.add(Annotation(xdb=None, gid=mgene,
                                                      direct=annotation.direct,
                                                      ref=annotation.ref,
                                                      evidence=annotation.evidence,
                                                      date=annotation.date,
                                                      cross_annotated=annotation.cross_annotated))
        self.annotations = mapped_annotations_set

    def get_annotated_genes(self, include_cross_annotated=True):
        genes = []
        for annotation in self.annotations:
            if (not include_cross_annotated) and annotation.cross_annotated:
                continue
            genes.append(annotation.gid)
        return genes

    def remove_annotation(self, annot):
        try:
            self.annotations.remove(annot)
        except KeyError:
            return

    def add_annotation(self, gid, ref=None, cross_annotated=False,
                       allow_duplicate_gid=True, origin=None, ortho_evidence=None):
        if not allow_duplicate_gid:
            for annotated in self.annotations:
                if annotated.gid == gid:
                    return
        self.annotations.add(
            Annotation(
                gid=gid,
                ref=ref,
                cross_annotated=cross_annotated,
                origin=origin,
                ortho_evidence=ortho_evidence))

    def get_annotation_size(self):
        return len(self.annotations)

    def get_namespace(self):
        return self.namespace

    def get_xrefs(self, dbid):
        if dbid in self.xrefs:
            return self.xrefs[dbid]
        else:
            return None

if __name__ == '__main__':
    from optparse import OptionParser

    usage = "usage: %prog [options]"
    parser = OptionParser(usage, version="%prog dev-unreleased")
    parser.add_option(
        "-o",
        "--obo-file",
        dest="obo",
        help="obo file",
        metavar="FILE")
    parser.add_option(
        "-a",
        "--association-file",
        dest="ass",
        help="gene association file",
        metavar="FILE")
    parser.add_option(
        "-b",
        dest="term_col",
        type="int",
        help="What column of the annotations file contains the term identifiers?",
        default=4)
    parser.add_option(
        "-g",
        dest="gcol",
        type="int",
        help="What column of the annotations file contains the desired identifiers?",
        default=1)
    parser.add_option(
        "-d",
        "--output-prefix",
        dest="opref",
        help="prefix for output files",
        metavar="string")
    parser.add_option(
        "-f",
        "--output-filename",
        dest="ofile",
        help="If given outputs all go term/gene annotation pairs to this file, file is created in the output prefix directory.",
        metavar="string")
    parser.add_option(
        "-i",
        "--id-file",
        dest="idfile",
        help="file to map excisting gene ids to the desired identifiers in the format <gene id>\\t<desired id>\\n",
        metavar="FILE")
    parser.add_option(
        "-p",
        action="store_true",
        dest="propagate",
        help="Should we progagate gene annotations?")
    parser.add_option(
        "-P",
        "--prune",
        dest="prune",
        help="A python string that will be evaled to decide if a node should be pruned.  Available variables are 'total' and 'direct' which are the total number of annotations and the number of direct annotations.")
    parser.add_option(
        "-t",
        "--slim-file",
        dest="slim",
        help="GO slim file contains GO terms to output, if not given outputs all GO terms",
        metavar="FILE")
    parser.add_option(
        "-n",
        "--namespace",
        dest="nspace",
        help="limit the GO term output to the input namespace: (biological_process, cellular_component, molecular_function)",
        metavar="STRING")
    parser.add_option(
        "-r",
        dest="refids",
        action="store_true",
        help="If given keeps track of ref IDs (e.g. PMIDs) for each go term and prints to standard out")
    parser.add_option(
        "-c",
        dest="check_fringe",
        action="store_true",
        help="Is the given slim file a true fringe in the given obo file?  Prints the result and exits.")
    parser.add_option(
        "-j",
        "--json-file",
        dest="json",
        help="file to output ontology (as json) to.")
    parser.add_option(
        "-A",
        dest="assoc_format",
        action="store_true",
        help="If we are printing to a file (-f), pass this to get a full association file back.")
    parser.add_option(
        "-l",
        dest="desc",
        action="store_true",
        help="Get descendents of terms")
    parser.add_option(
        "-u",
        dest="pub_filter",
        action="store_true",
        help="Filter annotations from high-throughput publications (>50 annotations)")
    (options, args) = parser.parse_args()

    if options.obo is None:
        sys.stderr.write("--obo file is required.\n")
        sys.exit()
    if options.check_fringe is None and options.ass is None:
        sys.stderr.write("--association file is required.\n")
        sys.exit()
    if options.desc is None and options.check_fringe is None and options.opref is None and not options.refids:
        sys.stderr.write("--prefix is required.\n")
        sys.exit()
    if options.check_fringe and options.slim is None:
        sys.stderr.write("--When checking fringe, must provide slim file.\n")
        sys.exit()
    if options.pub_filter and options.nspace is None:
        sys.stderr.write(
            "--When filtering by publication, must provide GO namespace.\n")
        sys.exit()

    id_name = None
    if options.idfile is not None:
        id_name = IDMap(options.idfile)

    gene_ontology = go(options.obo)

    # only check if fringe is valid in this obo file?
    if options.check_fringe:
        if gene_ontology.check_fringe(options.slim, options.nspace):
            print "A complete fringe"
        else:
            print "not a fringe"
        # now exit
        sys.exit(0)

    gene_ontology.populate_annotations(
        options.ass,
        gene_col=options.gcol,
        term_col=options.term_col)

    if options.pub_filter:
        pub_counts = defaultdict(set)
        for (term_id, term) in gene_ontology.go_terms.iteritems():
            if term.namespace != options.nspace:
                continue
            for a in term.annotations:
                pub_counts[a.ref].add((term, a))
        for (ref, annots) in pub_counts.iteritems():
            if len(annots) > 50:
                logger.info(
                    'Removing %i annotations from: %s',
                    ref,
                    len(annots))
                for (term, a) in annots:
                    term.remove_annotation(a)

    if options.idfile is not None:
        gene_ontology.map_genes(id_name)

    if options.propagate:
        gene_ontology.propagate()

    if options.prune and not options.slim:
        gene_ontology.prune(options.prune,)

    if options.json:
        jsonstr = gene_ontology.to_json()
        f = open(options.json, 'w')
        f.write(jsonstr)
        f.close()

    if options.slim:
        f = open(options.slim, 'r')
        gterms = []
        for line in f:
            fields = line.rstrip('\n').split('\t')
            gterms.append(fields[1])
        f.close()

        if options.desc:
            out = open('slim_descendents.txt', 'w')
            desc_terms = set()
            for term in gterms:
                cur_desc = set()
                cur_desc |= gene_ontology.get_descendents(term)
                desc_terms |= cur_desc
                cur_desc.add(term)
                desc_terms.add(term)
                for cur_term in cur_desc:
                    if cur_term in gene_ontology.go_terms:
                        out.write(
                            term +
                            '\t' +
                            gene_ontology.go_terms[term].name +
                            '\t' +
                            cur_term +
                            '\t' +
                            gene_ontology.go_terms[cur_term].name +
                            '\n')

            for term in desc_terms:
                if term in gene_ontology.go_terms:
                    term = gene_ontology.go_terms[term]
                    print term.name, term.go_id
            sys.exit(0)

        # should I only print ref IDs?
        if options.refids:
            gene_ontology.print_refids(gterms, options.nspace)
        elif options.ofile:
            gene_ontology.print_to_single_file(
                options.opref +
                '/' +
                options.ofile,
                gterms,
                options.nspace,
                options.assoc_format)
        else:
            gene_ontology.print_terms(options.opref, gterms, options.nspace)
    else:
        if options.refids:
            gene_ontology.print_refids(None, options.nspace)
        elif options.ofile:
            gene_ontology.print_to_single_file(
                options.opref +
                '/' +
                options.ofile,
                None,
                options.nspace,
                options.assoc_format)
        else:
            gene_ontology.print_terms(options.opref, None, options.nspace)

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import ec

from umbral.bignum import BigNum
from umbral.point import Point
from umbral.utils import poly_eval, lambda_coeff, hash_to_bn, kdf

class UmbralParameters(object):
    def __init__(self):
        self.curve = ec.SECP256K1()
        self.g = Point.get_generator_from_curve(self.curve)
        self.order = Point.get_order_from_curve(self.curve)
        self.h = Point.gen_rand(self.curve)
        self.u = Point.gen_rand(self.curve)

class KFrag(object):
    def __init__(self, id_, key, x, u1, z1, z2):
        self.bn_id = id_
        self.point_key = key
        self.point_eph_ni = x
        self.point_commitment = u1
        self.bn_sig1 = z1
        self.bn_sig2 = z2

    @classmethod
    def from_bytes(cls, data: bytes, curve):
        """
        Instantiate a KFrag object from the serialized data.
        """
        id = BigNum.from_bytes(data[0:32], curve)
        key = BigNum.from_bytes(data[32:64], curve)
        eph_ni = Point.from_bytes(data[64:97], curve)
        commitment = Point.from_bytes(data[97:130], curve)
        sig1 = BigNum.from_bytes(data[130:162], curve)
        sig2 = BigNum.from_bytes(data[162:194], curve)

        return KFrag(id, key, eph_ni, commitment, sig1, sig2)

    def to_bytes(self):
        """
        Serialize the KFrag into a bytestring.
        """
        id = self.bn_id.to_bytes()
        key = self.point_key.to_bytes()
        eph_ni = self.point_eph_ni.to_bytes()
        commitment = self.point_commitment.to_bytes()
        sig1 = self.bn_sig1.to_bytes()
        sig2 = self.bn_sig2.to_bytes()

        return id + key + eph_ni + commitment + sig1 + sig2

    def verify(self, pub_a, params: UmbralParameters):

        u1 = self.point_commitment
        z1 = self.bn_sig1
        z2 = self.bn_sig2
        x  = self.point_eph_ni

        y = (params.g * z2) + (pub_a * z1)

        return z1 == hash_to_bn([x, u1, y, self.bn_id], params)
    
    def is_consistent(self, vKeys, params: UmbralParameters):
        if vKeys is None or len(vKeys) == 0:
            raise ValueError('vKeys must not be empty')

        # TODO: change this!
        h = params.h
        lh_exp = h * self.point_key

        rh_exp = vKeys[0]
        i_j = self.bn_id
        for vKey in vKeys[1:]:
            rh_exp = rh_exp + (vKey * i_j)
            i_j = i_j * self.bn_id

        return lh_exp == rh_exp

    def __bytes__(self):
        return self.to_bytes()


class CapsuleFrag(object):
    def __init__(self, e1, v1, id_, x):
        self.e1 = e1
        self.v1 = v1
        self.bn_kfrag_id = id_
        self.point_eph_ni = x

class Capsule(object):
    def __init__(self, point_eph_e, point_eph_v, bn_sig):
        self.point_eph_e = point_eph_e
        self.point_eph_v = point_eph_v
        self.bn_sig = bn_sig

        self.cfrags = {}

    def verify(self, params: UmbralParameters):

        e = self.point_eph_e
        v = self.point_eph_v
        s = self.bn_sig
        h = hash_to_bn([e, v], params)

        return params.g * s == v + (e * h)

    def attach_cfrag(self, cfrag: CapsuleFrag):
        self.cfrags[cfrag.bn_kfrag_id] = cfrag

    def reconstruct(self):
        id_cfrag_pairs = list(self.cfrags.items())
        id_0, cfrag_0 = id_cfrag_pairs[0]
        if len(id_cfrag_pairs) > 1:
            ids = self.cfrags.keys() 
            lambda_0 = lambda_coeff(id_0, ids)
            e = cfrag_0.e1 * lambda_0
            v = cfrag_0.v1 * lambda_0
            
            for id_i,cfrag in id_cfrag_pairs[1:]:
                lambda_i = lambda_coeff(id_i, ids)
                e = e + (cfrag.e1 * lambda_i)
                v = v + (cfrag.v1 * lambda_i)
        else:
            e = cfrag_0.e1
            v = cfrag_0.v1
        
        return ReconstructedCapsule(e_prime=e, v_prime=v, x=cfrag_0.point_eph_ni)


class ReconstructedCapsule(object):
    def __init__(self, e_prime, v_prime, x):
        self.e_prime = e_prime
        self.v_prime = v_prime
        self.point_eph_ni = x


class ChallengeResponse(object):
    def __init__(self, e2, v2, u1, u2, z1, z2, z3):
        self.e2 = e2
        self.v2 = v2
        self.point_kfrag_commitment = u1
        self.point_kfrag_pok = u2
        self.bn_kfrag_sig1 = z1
        self.bn_kfrag_sig2 = z2
        self.bn_sig = z3


class PRE(object):
    def __init__(self, params: UmbralParameters):
        self.params = params

    def gen_priv(self):
        return BigNum.gen_rand(self.params.curve)

    def priv2pub(self, priv):
        g = self.params.g
        return g * priv

    def split_rekey(self, priv_a, pub_b, threshold, N):
        g = self.params.g
        x = BigNum.gen_rand(self.params.curve)
        xcomp = g * x
        d = hash_to_bn([xcomp, pub_b, pub_b * x], self.params)

        coeffs = [priv_a * (~d)]
        coeffs += [BigNum.gen_rand(self.params.curve) for _ in range(threshold - 1)]

        h = self.params.h
        u = self.params.u

        vKeys = [h * coeff for coeff in coeffs]

        rk_shares = []
        for _ in range(N):
            id_ = BigNum.gen_rand(self.params.curve)
            rk = poly_eval(coeffs, id_)

            u1 = u * rk
            y = BigNum.gen_rand(self.params.curve)

            z1 = hash_to_bn([xcomp, u1, g * y, id_], self.params)
            z2 = y - priv_a * z1

            kFrag = KFrag(id_=id_, key=rk, x=xcomp, u1=u1, z1=z1, z2=z2)
            rk_shares.append(kFrag)

        return rk_shares, vKeys

    def reencrypt(self, kFrag, capsule):
        # TODO: Put the assert at the end, but exponentiate by a randon number when false?
        assert capsule.verify(self.params), "Generic Umbral Error"
        
        e1 = capsule.point_eph_e * kFrag.point_key
        v1 = capsule.point_eph_v * kFrag.point_key

        cFrag = CapsuleFrag(e1=e1, v1=v1, id_=kFrag.bn_id, x=kFrag.point_eph_ni)
        return cFrag

    def challenge(self, rk, capsule, cFrag):


        e1 = cFrag.e1
        v1 = cFrag.v1

        e = capsule.point_eph_e
        v = capsule.point_eph_v

        u = self.params.u
        u1 = rk.point_commitment

        t = BigNum.gen_rand(self.params.curve)
        e2 = e * t
        v2 = v * t
        u2 = u * t

        h = hash_to_bn([e, e1, e2, v, v1, v2, u, u1, u2], self.params)

        z3 = t + h * rk.point_key

        ch_resp = ChallengeResponse(e2=e2, v2=v2, u1=u1, u2=u2, z1=rk.bn_sig1, z2=rk.bn_sig2, z3=z3)

        # Check correctness of original ciphertext (check nº 2) at the end 
        # to avoid timing oracles
        assert capsule.verify(self.params), "Generic Umbral Error"
        return ch_resp

    def check_challenge(self, capsule, cFrag, challenge_resp, pub_a):
        e = capsule.point_eph_e
        v = capsule.point_eph_v

        e1 = cFrag.e1
        v1 = cFrag.v1
        xcomp = cFrag.point_eph_ni
        re_id = cFrag.bn_kfrag_id

        e2 = challenge_resp.e2
        v2 = challenge_resp.v2

        g = self.params.g

        u = self.params.u
        u1 = challenge_resp.point_kfrag_commitment
        u2 = challenge_resp.point_kfrag_pok

        z1 = challenge_resp.bn_kfrag_sig1
        z2 = challenge_resp.bn_kfrag_sig2
        z3 = challenge_resp.bn_sig

        ycomp = (g * z2) + (pub_a * z1)

        h = hash_to_bn([e, e1, e2, v, v1, v2, u, u1, u2], self.params)

        check31 = z1 == hash_to_bn([xcomp, u1, ycomp, re_id], self.params)
        check32 = e * z3 == e2 + (e1 * h)
        check33 = u * z3 == u2 + (u1 * h)

        return check31 & check32 & check33

    def encapsulate(self, pub_key, key_length=32):
        """Generates a symmetric key and its associated KEM ciphertext"""
        g = self.params.g

        priv_r = BigNum.gen_rand(self.params.curve)
        pub_r = g * priv_r

        priv_u = BigNum.gen_rand(self.params.curve)
        pub_u = g * priv_u

        h = hash_to_bn([pub_r, pub_u], self.params)
        s = priv_u + (priv_r * h)

        shared_key = pub_key * (priv_r + priv_u)

        # Key to be used for symmetric encryption
        key = kdf(shared_key, key_length)

        return key, Capsule(point_eph_e=pub_r, point_eph_v=pub_u, bn_sig=s)


    def decapsulate_original(self, priv_key, capsule, key_length=32):
        """Derive the same symmetric key"""

        shared_key = (capsule.point_eph_e + capsule.point_eph_v) * priv_key
        key = kdf(shared_key, key_length)

        # Check correctness of original ciphertext (check nº 2) at the end 
        # to avoid timing oracles
        assert capsule.verify(self.params), "Generic Umbral Error"
        return key

    def decapsulate_reencrypted(self, pub_key: Point, priv_key: BigNum, orig_pub_key: Point,
                                recapsule: ReconstructedCapsule, original_capsule: Capsule, key_length=32):
        """Derive the same symmetric key"""

        xcomp = recapsule.point_eph_ni
        d = hash_to_bn([xcomp, pub_key, xcomp * priv_key], self.params)

        e_prime = recapsule.e_prime
        v_prime = recapsule.v_prime

        shared_key = (e_prime + v_prime) * d
        key = kdf(shared_key, key_length)

        e = original_capsule.point_eph_e
        v = original_capsule.point_eph_v
        s = original_capsule.bn_sig
        h = hash_to_bn([e, v], self.params)
        inv_d = ~d
        assert orig_pub_key * (s * inv_d) == v_prime + (e_prime * h), "Generic Umbral Error"

        return key
